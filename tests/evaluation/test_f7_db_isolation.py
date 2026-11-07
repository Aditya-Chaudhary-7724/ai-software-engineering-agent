"""F7 evaluation database isolation — proven against a STAND-IN application
database, never the real development database.

`guard_db` is a dedicated database (`ai_swe_agent_eval_guard`) created on
the test server, given the application schema and sentinel rows in every
table the evaluation could touch. Each test passes it as the suite's
`database_url` (the role DATABASE_URL plays in a real run) and pytest's
database as the isolated one, then compares a checksum of every table.

Also made explicit here: what cleanup survives (an exception inside a
case) and what it cannot (a killed process), and that the leftover
report and `remove_leftovers` handle the latter without touching
anything outside an evaluation namespace.
"""

import shutil
import signal
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

import evaluation.suite as suite_module
from evaluation import cleanup
from evaluation.environment import neo4j_available
from evaluation.runners.answers import run_answer_evaluation
from evaluation.safety import UnsafeEvaluationTargetError
from vectorstore.migrations import apply_schema
from vectorstore.store import VectorStore

from tests.agent_tasks.conftest import clean_agent_tables  # noqa: F401  (fixture)
from tests.evaluation.conftest import requires_postgres
from tests.vectorstore.conftest import TEST_DATABASE_URL

GUARD_DB = "ai_swe_agent_eval_guard"
REPO_ROOT = Path(__file__).resolve().parents[2]
UNREACHABLE_URL = "postgresql://nobody@127.0.0.1:1/nowhere?connect_timeout=1"


def _url_for(dbname: str) -> str:
    info = psycopg.conninfo.conninfo_to_dict(TEST_DATABASE_URL)
    info["dbname"] = dbname
    return psycopg.conninfo.make_conninfo(**{k: str(v) for k, v in info.items() if v is not None})


def snapshot(database_url: str) -> dict:
    """Row count and content checksum of every table in `public`."""
    with psycopg.connect(database_url) as conn:
        tables = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")]
        result = {}
        for table in tables:
            row = conn.execute(
                psycopg.sql.SQL("SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) FROM {} t").format(
                    psycopg.sql.Identifier(table)
                )
            ).fetchone()
            result[table] = row
        return result


@pytest.fixture
def guard_db():
    try:
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
            if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (GUARD_DB,)).fetchone():
                admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(GUARD_DB)))
    except psycopg.errors.InsufficientPrivilege:
        pytest.skip("role may not CREATE DATABASE; the stand-in application database cannot be created")
    url = _url_for(GUARD_DB)
    with psycopg.connect(url) as conn:
        apply_schema(conn)
        conn.execute("TRUNCATE agent_tasks, jobs, code_chunks, repositories RESTART IDENTITY CASCADE")  # our own database
        repository_id = conn.execute(
            "INSERT INTO repositories (name, root_path) VALUES ('sentinel', '/srv/dev/sentinel') RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO code_chunks (repository_id, relative_path, chunk_type, start_line, end_line, content, embedding) "
            "VALUES (%s, 'app.py', 'module', 1, 1, 'print(1)', array_fill(0.1, ARRAY[1536])::vector)",
            (repository_id,),
        )
        conn.execute(
            "INSERT INTO agent_tasks (id, repository_id, target_path, instruction, route, status) "
            "VALUES ('00000000-0000-4000-8000-000000000001', %s, '/srv/dev/sentinel', 'x', 'answer', 'completed')",
            (repository_id,),
        )
        conn.commit()
    yield url


# -- routing ---------------------------------------------------------------------


@requires_postgres
def test_with_an_isolated_database_no_category_writes_to_the_application_database(guard_db, clean_agent_tables, monkeypatch):  # noqa: F811
    """The REAL runners, end to end: every category's rows go to the
    isolated database; the application database is byte-for-byte unchanged."""
    used_urls = []
    real_vector_store = suite_module.VectorStore
    monkeypatch.setattr(suite_module, "VectorStore", lambda url: used_urls.append(url) or real_vector_store(url))
    graph_before = _foreign_graph_node_count()
    before = snapshot(guard_db)

    report = suite_module.run_full_suite(guard_db, isolated_database_url=TEST_DATABASE_URL)

    assert snapshot(guard_db) == before
    assert used_urls == [TEST_DATABASE_URL]
    assert report.environment["evaluation_writes"] == "isolated database"
    assert report.environment["leftover_evaluation_repositories"] == "0"
    ran = [r for r in report.results if not r.skipped]
    assert {r.category for r in ran} >= {"retrieval", "rag", "answers", "modification_safety", "agent_lifecycle"}
    assert all(r.passed for r in ran), [(r.case_id, r.failure_reason) for r in ran if not r.passed]
    assert _foreign_graph_node_count() == graph_before  # Neo4j outside evaluation namespaces untouched


def _forbid_database_categories(monkeypatch):
    for name in ("run_retrieval_evaluation", "run_rag_evaluation", "run_answer_evaluation", "run_modification_safety_evaluation",
                 "run_agent_evaluation", "run_modification_evaluation", "run_testing_loop_evaluation"):
        monkeypatch.setattr(suite_module, name, lambda *a, _n=name, **k: pytest.fail(f"{_n} must not run"))
    monkeypatch.setattr(suite_module, "run_agent_lifecycle_evaluation", lambda *a, **k: [])


@requires_postgres
def test_an_unreachable_isolated_database_never_falls_back(guard_db, monkeypatch):
    _forbid_database_categories(monkeypatch)
    before = snapshot(guard_db)

    report = suite_module.run_full_suite(guard_db, isolated_database_url=UNREACHABLE_URL)

    database_results = [r for r in report.results if r.category not in ("agent_lifecycle", "regression")]
    assert database_results and all(r.skipped and "refusing to fall back" in r.skip_reason for r in database_results)
    assert snapshot(guard_db) == before


@requires_postgres
def test_an_isolated_url_that_is_the_application_database_is_refused(guard_db, monkeypatch):
    _forbid_database_categories(monkeypatch)
    respelled = guard_db.replace("host=localhost", "host=127.0.0.1") + " application_name=respelled"
    before = snapshot(guard_db)

    report = suite_module.run_full_suite(guard_db, isolated_database_url=respelled)

    database_results = [r for r in report.results if r.category not in ("agent_lifecycle", "regression")]
    assert all(r.skipped and r.skip_reason.startswith("REFUSED") for r in database_results)
    assert snapshot(guard_db) == before


def test_without_an_isolated_database_the_report_says_where_rows_went():
    url, writes_to = suite_module.select_evaluation_database("postgresql://app/x", None)
    assert url == "postgresql://app/x"
    assert writes_to.startswith("DATABASE_URL")


# -- guarded cleanup -----------------------------------------------------------------


@requires_postgres
def test_cleanup_refuses_to_delete_a_development_repository(guard_db):
    """Even given a real repository's id, cleanup deletes nothing."""
    store = VectorStore(guard_db)
    with psycopg.connect(guard_db) as conn:
        sentinel_id = conn.execute("SELECT id FROM repositories WHERE root_path = '/srv/dev/sentinel'").fetchone()[0]
    before = snapshot(guard_db)

    with pytest.raises(UnsafeEvaluationTargetError):
        cleanup.delete_evaluation_repository(store, sentinel_id)
    assert snapshot(guard_db) == before


def test_graph_cleanup_refuses_a_non_evaluation_namespace_without_querying():
    class RecordingClient:
        calls = []

        def run(self, query, **params):
            self.calls.append(query)
            return []

    client = RecordingClient()
    with pytest.raises(UnsafeEvaluationTargetError):
        cleanup.delete_evaluation_graph(client, "/srv/dev/sentinel")
    assert client.calls == []


# -- failure and interruption ----------------------------------------------------------


def _eval_rows(database_url: str) -> int:
    with psycopg.connect(database_url) as conn:
        return conn.execute("SELECT count(*) FROM repositories WHERE root_path LIKE '%/eval-%'").fetchone()[0]


@requires_postgres
def test_a_case_that_raises_still_cleans_up(monkeypatch):
    from hybrid.service import HybridRAGService

    def explode(self, *args, **kwargs):
        raise RuntimeError("simulated failure inside a case")

    monkeypatch.setattr(HybridRAGService, "answer", explode)
    before = _eval_rows(TEST_DATABASE_URL)
    with pytest.raises(RuntimeError, match="simulated failure"):
        run_answer_evaluation(VectorStore(TEST_DATABASE_URL))
    assert _eval_rows(TEST_DATABASE_URL) == before


_KILLED_RUN = """
import os, signal, sys, tempfile
sys.path.insert(0, sys.argv[2])
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore
root = tempfile.mkdtemp(prefix="eval-killed-")
open(os.path.join(root, "main.py"), "w").write("def greet():\\n    return 'hi'\\n")
IndexingService(DeterministicLocalEmbeddingProvider(), VectorStore(sys.argv[1])).index_repository(root)
print(root, flush=True)
os.kill(os.getpid(), signal.SIGKILL)  # no finally block runs
"""


@requires_postgres
def test_a_killed_run_leaves_namespaced_rows_that_are_reported_and_removable():
    """The documented limitation, demonstrated: a SIGKILLed process cannot
    clean up. Its rows are found by the leftover report and removed by
    `remove_leftovers`, which leaves non-evaluation rows alone."""
    store = VectorStore(TEST_DATABASE_URL)
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        conn.execute("INSERT INTO repositories (name, root_path) VALUES ('keep', '/srv/dev/keep-me') ON CONFLICT DO NOTHING")
        conn.commit()
    killed = subprocess.run(
        [sys.executable, "-c", _KILLED_RUN, TEST_DATABASE_URL, str(REPO_ROOT / "backend")],
        capture_output=True, text=True, timeout=120,
    )
    root = killed.stdout.strip()
    try:
        assert killed.returncode == -signal.SIGKILL, killed.stderr
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            left_id = conn.execute("SELECT id FROM repositories WHERE root_path = %s", (str(Path(root).resolve()),)).fetchone()
            if left_id is None:
                left_id = conn.execute("SELECT id FROM repositories WHERE root_path = %s", (root,)).fetchone()
        assert left_id is not None, "the killed run's row should still be there (cleanup cannot run)"
        assert left_id[0] in cleanup.find_leftover_repositories(store)

        removed = cleanup.remove_leftovers(store)

        assert removed["repositories"] >= 1
        assert cleanup.find_leftover_repositories(store) == []
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            assert conn.execute("SELECT count(*) FROM repositories WHERE root_path = '/srv/dev/keep-me'").fetchone()[0] == 1
    finally:
        if root:
            shutil.rmtree(root, ignore_errors=True)
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            conn.execute("DELETE FROM repositories WHERE root_path = '/srv/dev/keep-me'")
            conn.commit()


# -- Neo4j ---------------------------------------------------------------------------------


def _foreign_graph_node_count():
    """Nodes outside every evaluation namespace (None without Neo4j)."""
    if not neo4j_available():
        return None
    from graph.client import Neo4jClient

    with Neo4jClient() as client:
        rows = client.run(
            "MATCH (n) WHERE NOT coalesce(n.repository_root_path, n.root_path, '') CONTAINS $marker RETURN count(n) AS c",
            marker=f"/{cleanup.EVALUATION_PREFIX}",
        )
    return rows[0]["c"]


def test_every_graph_node_key_is_scoped_to_its_repository():
    """Why namespacing is sound: every node the graph builder MERGEs is
    keyed by the repository root, so an evaluation repository can never
    MERGE onto (and later delete) another repository's node."""
    source = (REPO_ROOT / "backend" / "graph" / "builder.py").read_text()
    merges = [line.strip() for line in source.splitlines() if line.strip().startswith("MERGE (") and ":" in line.split(")")[0]]
    assert merges
    for merge in merges:
        assert "repository_root_path: $root_path" in merge or "Repository {root_path: $root_path}" in merge, merge


@requires_postgres
def test_cli_exits_2_when_the_isolated_database_is_unreachable(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_evaluation_cli", REPO_ROOT / "backend" / "scripts" / "run_evaluation.py")
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "run_full_suite", lambda **kwargs: pytest.fail("no evaluation may run"))
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setattr(sys, "argv", ["run_evaluation.py", "--isolated-database-url", UNREACHABLE_URL])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 2


# -- regression runner: which database the re-run suites may touch ----------------------


def _fake_pytest_run(captured):
    """Stands in for `subprocess.run` of pytest: records the environment and
    writes a one-test passing JUnit report where pytest would have."""

    def run(cmd, **kwargs):
        captured.append(kwargs["env"])
        report = next(arg.split("=", 1)[1] for arg in cmd if arg.startswith("--junitxml="))
        Path(report).write_text('<testsuite tests="1" failures="0" errors="0" skipped="0"></testsuite>')
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return run


def test_regression_subprocess_pins_every_database_variable_to_the_verified_target(monkeypatch):
    """API routes read DATABASE_URL per request. The child must never
    inherit the developer's real one (exported from .env)."""
    from evaluation.runners import regression

    monkeypatch.setenv("DATABASE_URL", "postgresql://dev-user@localhost:5432/real_development_db")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://someone@localhost:5432/something_else")
    captured = []
    monkeypatch.setattr(regression.subprocess, "run", _fake_pytest_run(captured))

    result = regression._run_group("regression-demo", "demo", ("tests/agent_tasks",), "postgresql://eval@localhost:5432/isolated")

    assert result.passed
    assert captured[0]["DATABASE_URL"] == "postgresql://eval@localhost:5432/isolated"
    assert captured[0]["TEST_DATABASE_URL"] == "postgresql://eval@localhost:5432/isolated"


def test_regression_refuses_when_its_database_is_unreachable(monkeypatch):
    """Fail closed: an unreachable target cannot be verified as isolated."""
    from evaluation.runners import regression

    monkeypatch.setenv("TEST_DATABASE_URL", UNREACHABLE_URL)
    monkeypatch.setattr(regression, "_run_group", lambda *a: pytest.fail("suites must not run"))

    results = regression.run_regression_evaluation(protected_database_url="postgresql://app@localhost:5432/app")

    assert len(results) == len(regression.GROUPS)
    assert all(r.skipped and r.skip_reason.startswith("REFUSED") and "not reachable" in r.skip_reason for r in results)


def test_regression_refuses_an_unreachable_isolated_database_instead_of_using_test_database_url(monkeypatch):
    from evaluation.runners import regression

    monkeypatch.setattr(regression, "_run_group", lambda *a: pytest.fail("suites must not run"))
    results = regression.run_regression_evaluation(
        protected_database_url="postgresql://app@localhost:5432/app", isolated_database_url=UNREACHABLE_URL
    )
    assert all(r.skipped and "(isolated evaluation database) is not reachable" in r.skip_reason for r in results)


@requires_postgres
def test_regression_with_an_isolated_database_runs_every_group_there(guard_db, monkeypatch):
    from evaluation.runners import regression

    monkeypatch.setenv("TEST_DATABASE_URL", UNREACHABLE_URL)  # proves it is not consulted
    targets = []
    monkeypatch.setattr(regression, "_run_group", lambda case_id, description, paths, url: targets.append(url))

    regression.run_regression_evaluation(protected_database_url=guard_db, isolated_database_url=TEST_DATABASE_URL)

    assert targets == [TEST_DATABASE_URL] * len(regression.GROUPS)


_CANARY_TEST = '''
import os, psycopg

def test_writes_through_ambient_database_url():
    # Deliberately does NOT monkeypatch DATABASE_URL — the mistake the
    # regression runner must make harmless.
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("INSERT INTO repositories (name, root_path) VALUES ('canary', %s)", (os.environ["CANARY_ROOT"],))
'''


@requires_postgres
def test_a_regression_test_that_forgets_to_override_database_url_cannot_reach_the_application_database(
    guard_db, tmp_path, monkeypatch
):
    """End to end, real pytest subprocess: the ambient DATABASE_URL is the
    stand-in application database (as `.env` would export the real one).
    A test writing through it lands in the isolated database instead."""
    from evaluation.runners import regression

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_canary.py").write_text(_CANARY_TEST)
    canary_root = f"/srv/canary/{tmp_path.name}"
    monkeypatch.setattr(regression, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(regression, "GROUPS", [("regression-canary", "canary", ("tests/test_canary.py",))])
    monkeypatch.setenv("DATABASE_URL", guard_db)
    monkeypatch.setenv("CANARY_ROOT", canary_root)
    before = snapshot(guard_db)
    try:
        (result,) = regression.run_regression_evaluation(protected_database_url=guard_db, isolated_database_url=TEST_DATABASE_URL)

        assert result.passed, result.failure_reason
        assert snapshot(guard_db) == before
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            assert conn.execute("SELECT count(*) FROM repositories WHERE root_path = %s", (canary_root,)).fetchone()[0] == 1
    finally:
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            conn.execute("DELETE FROM repositories WHERE root_path = %s", (canary_root,))
            conn.commit()


@requires_postgres
def test_destructive_regression_suites_leave_the_application_database_unchanged(guard_db, monkeypatch):
    """A real group whose fixtures DELETE every agent_tasks row, with the
    stand-in application database as the ambient DATABASE_URL."""
    from evaluation.runners import regression

    monkeypatch.setattr(regression, "GROUPS", [("regression-approval-http", "F6 HTTP", ("tests/api/test_v1_agent_tasks.py",))])
    monkeypatch.setenv("DATABASE_URL", guard_db)
    before = snapshot(guard_db)

    (result,) = regression.run_regression_evaluation(protected_database_url=guard_db, isolated_database_url=TEST_DATABASE_URL)

    assert result.passed, result.failure_reason
    assert next(m for m in result.metrics if m.name == "tests_passed").value > 0
    assert snapshot(guard_db) == before


# -- interruption beyond an ordinary exception ------------------------------------------


@requires_postgres
def test_ctrl_c_inside_a_case_still_cleans_up(monkeypatch):
    """KeyboardInterrupt is a BaseException (not caught by `except
    Exception`), but `finally` cleanup still runs."""
    from hybrid.service import HybridRAGService

    def interrupt(self, *args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(HybridRAGService, "answer", interrupt)
    before = _eval_rows(TEST_DATABASE_URL)
    with pytest.raises(KeyboardInterrupt):
        run_answer_evaluation(VectorStore(TEST_DATABASE_URL))
    assert _eval_rows(TEST_DATABASE_URL) == before


@requires_postgres
def test_ctrl_c_inside_a_lifecycle_case_removes_its_task_and_repository(guard_db, clean_agent_tables, monkeypatch):  # noqa: F811
    from evaluation.runners import agent_lifecycle

    def interrupted_case(h):
        store, _ = h.executor()
        with h.repository() as (root, repository_id):
            h.submit(store, root, repository_id)
            raise KeyboardInterrupt

    monkeypatch.setattr(agent_lifecycle, "CASES", [("lifecycle-interrupted", "Ctrl-C mid-case", interrupted_case)])
    rows_before = _eval_rows(TEST_DATABASE_URL)

    with pytest.raises(KeyboardInterrupt):
        agent_lifecycle.run_agent_lifecycle_evaluation(TEST_DATABASE_URL, protected_database_url=guard_db)

    with psycopg.connect(TEST_DATABASE_URL) as conn:
        assert conn.execute("SELECT count(*) FROM agent_tasks").fetchone()[0] == 0
    assert _eval_rows(TEST_DATABASE_URL) == rows_before


_TERMINATED_RUN = """
import os, signal, sys, tempfile, time
sys.path.insert(0, sys.argv[2])
from evaluation.cleanup import delete_evaluation_repository, exit_on_sigterm
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore
if sys.argv[3] == "handler":
    exit_on_sigterm()
store = VectorStore(sys.argv[1])
root = tempfile.mkdtemp(prefix="eval-terminated-")
open(os.path.join(root, "main.py"), "w").write("def greet():\\n    return 'hi'\\n")
repository_id = IndexingService(DeterministicLocalEmbeddingProvider(), store).index_repository(root).repository_id
print(root, flush=True)
try:
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(30)
finally:
    delete_evaluation_repository(store, repository_id)
"""


@requires_postgres
@pytest.mark.parametrize("mode", ["handler", "default"])
def test_sigterm_cleans_up_only_with_the_cli_handler(mode):
    """With `exit_on_sigterm` (what the CLI installs) `finally` cleanup
    runs; with Python's default SIGTERM action it does not — the limitation
    the handler closes, shown explicitly."""
    store = VectorStore(TEST_DATABASE_URL)
    run = subprocess.run(
        [sys.executable, "-c", _TERMINATED_RUN, TEST_DATABASE_URL, str(REPO_ROOT / "backend"), mode],
        capture_output=True, text=True, timeout=120,
    )
    root = run.stdout.strip()
    try:
        assert root, run.stderr
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            left = conn.execute(
                "SELECT count(*) FROM repositories WHERE root_path IN (%s, %s)", (root, str(Path(root).resolve()))
            ).fetchone()[0]
        if mode == "handler":
            assert run.returncode == 128 + signal.SIGTERM, run.stderr
            assert left == 0
        else:
            assert run.returncode == -signal.SIGTERM, run.stderr
            assert left == 1, "default SIGTERM skips `finally`: the row is left behind"
    finally:
        cleanup.remove_leftovers(store)
        shutil.rmtree(root, ignore_errors=True)
