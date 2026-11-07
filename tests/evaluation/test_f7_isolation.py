"""F7 isolation regressions — one test (at least) per defect found while
completing F7:

1. The lifecycle runner's cases leaked state: `lifecycle-concurrent-decisions`
   left its task queued, so the next case's worker claimed it and
   `lifecycle-worker-interruption` passed without its approval ever being
   recorded.
2. "Is this the application's database?" was a URL string comparison, so
   `localhost` vs `127.0.0.1` (or any re-spelling) bypassed it.
3. The regression runner re-runs suites whose fixtures delete every
   `agent_tasks` row in TEST_DATABASE_URL, with no check that it is not
   the application's database.
4. The regression runner silently dropped missing test paths (and with all
   paths missing would have run the WHOLE suite under the group's name).
5. The isolated database being unreachable crashed the whole suite.
"""

import importlib.util
import sys
from pathlib import Path

import psycopg
import pytest

from evaluation.runners import agent_lifecycle, regression
from evaluation.safety import UnsafeEvaluationTargetError, database_identity, require_distinct_database

from tests.agent_tasks.conftest import clean_agent_tables  # noqa: F401  (fixture)
from tests.evaluation.conftest import requires_postgres
from tests.vectorstore.conftest import TEST_DATABASE_URL

UNREACHABLE_URL = "postgresql://nobody@127.0.0.1:1/nowhere?connect_timeout=1"


def _respelled(url: str, **changes: str) -> str:
    """The same database under a different connection string (keyword form,
    127.0.0.1 instead of localhost, an extra parameter) unless `changes`
    names another database."""
    info = psycopg.conninfo.conninfo_to_dict(url)
    if info.get("host") in (None, "localhost"):
        info["host"] = "127.0.0.1"
    info["application_name"] = "f7-isolation-test"
    info.update(changes)
    return psycopg.conninfo.make_conninfo(**{k: str(v) for k, v in info.items() if v is not None})


def _active_tasks() -> int:
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        row = conn.execute("SELECT count(*) FROM agent_tasks WHERE status IN ('queued','running','awaiting_approval')").fetchone()
        return int(row[0]) if row else 0


# -- database identity ----------------------------------------------------------


@requires_postgres
def test_the_same_database_spelled_differently_is_refused():
    variant = _respelled(TEST_DATABASE_URL)
    assert variant != TEST_DATABASE_URL
    assert database_identity(variant) == database_identity(TEST_DATABASE_URL)
    with pytest.raises(UnsafeEvaluationTargetError, match="same database"):
        require_distinct_database(variant, TEST_DATABASE_URL, purpose="test")


@requires_postgres
def test_a_different_database_on_the_same_server_is_accepted():
    other = _respelled(TEST_DATABASE_URL, dbname="postgres")
    try:
        database_identity(other)
    except psycopg.OperationalError:
        pytest.skip("maintenance database 'postgres' not reachable with these credentials")
    require_distinct_database(TEST_DATABASE_URL, other, purpose="test")


def test_an_unreachable_application_database_fails_closed():
    with pytest.raises(UnsafeEvaluationTargetError, match="cannot be verified"):
        require_distinct_database("postgresql://ignored/x", UNREACHABLE_URL, purpose="test")


def test_nothing_to_protect_needs_no_connection():
    require_distinct_database(UNREACHABLE_URL, None, purpose="test")
    require_distinct_database(UNREACHABLE_URL, "", purpose="test")


def test_identical_strings_are_refused_without_connecting():
    with pytest.raises(UnsafeEvaluationTargetError, match="must differ"):
        require_distinct_database(UNREACHABLE_URL, UNREACHABLE_URL, purpose="test")


# -- agent lifecycle --------------------------------------------------------------


@requires_postgres
def test_lifecycle_refuses_the_application_database_under_another_spelling(clean_agent_tables):  # noqa: F811
    results = agent_lifecycle.run_agent_lifecycle_evaluation(_respelled(TEST_DATABASE_URL), protected_database_url=TEST_DATABASE_URL)
    assert len(results) == len(agent_lifecycle.CASES)
    assert all(r.skipped and r.skip_reason.startswith("REFUSED") for r in results)
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        assert conn.execute("SELECT count(*) FROM agent_tasks").fetchone()[0] == 0  # nothing was created


def test_lifecycle_with_an_unreachable_isolated_database_is_skipped_not_crashed():
    results = agent_lifecycle.run_agent_lifecycle_evaluation(UNREACHABLE_URL, protected_database_url=None)
    assert len(results) == len(agent_lifecycle.CASES)
    assert all(r.skipped and "not reachable" in r.skip_reason for r in results)


@requires_postgres
def test_a_case_that_leaves_a_task_active_fails_and_cannot_affect_the_next(clean_agent_tables, monkeypatch):  # noqa: F811
    def leaky(h):
        store, _ = h.executor()
        with h.repository() as (root, repository_id):
            h.submit(store, root, repository_id)  # queued, never processed
        return []

    approve = next(c for c in agent_lifecycle.CASES if c[0] == "lifecycle-approve-apply")
    monkeypatch.setattr(agent_lifecycle, "CASES", [("leaky", "leaves its task queued", leaky), approve])

    results = {r.case_id: r for r in agent_lifecycle.run_agent_lifecycle_evaluation(TEST_DATABASE_URL, protected_database_url=None)}

    assert results["leaky"].passed is False
    assert "case_left_no_active_task" in (results["leaky"].failure_reason or "")
    assert results["lifecycle-approve-apply"].passed is True  # the leftover was terminated, not claimed
    assert _active_tasks() == 0


@requires_postgres
def test_interruption_case_fails_if_no_approval_was_recorded(clean_agent_tables, monkeypatch):  # noqa: F811
    """Before the fix this case PASSED with every decision refused: its
    worker had claimed a leftover task, and nothing checked that the
    approval it set out to interrupt had been recorded."""
    from agent_tasks.store import AgentTaskStore

    monkeypatch.setattr(AgentTaskStore, "record_decision", lambda self, task_id, **kwargs: None)
    interruption = next(c for c in agent_lifecycle.CASES if c[0] == "lifecycle-worker-interruption")
    monkeypatch.setattr(agent_lifecycle, "CASES", [interruption])

    (result,) = agent_lifecycle.run_agent_lifecycle_evaluation(TEST_DATABASE_URL, protected_database_url=None)

    assert result.passed is False
    assert "approval_recorded_before_worker_died" in (result.failure_reason or "")


@requires_postgres
def test_every_lifecycle_case_processes_only_its_own_task(clean_agent_tables, monkeypatch):  # noqa: F811
    """The exact failure mode of defect 1: record which task every worker
    claim returns; each must belong to the case that is running."""
    from agent_tasks.store import AgentTaskStore

    created, claims, current = {}, [], {"case": None}
    real_claim, real_submit = AgentTaskStore.claim_next, agent_lifecycle._Harness.try_submit

    def claim(self, worker_id):
        task = real_claim(self, worker_id)
        if task is not None:
            claims.append((current["case"], task.id))
        return task

    def submit(self, store, root, repository_id):
        task = real_submit(self, store, root, repository_id)
        if task is not None:
            created[task.id] = current["case"]
        return task

    def tracking(case_id, fn):
        def run(h):
            current["case"] = case_id
            return fn(h)

        return run

    monkeypatch.setattr(AgentTaskStore, "claim_next", claim)
    monkeypatch.setattr(agent_lifecycle._Harness, "try_submit", submit)
    monkeypatch.setattr(agent_lifecycle, "CASES", [(cid, d, tracking(cid, fn)) for cid, d, fn in agent_lifecycle.CASES])

    results = agent_lifecycle.run_agent_lifecycle_evaluation(TEST_DATABASE_URL, protected_database_url=None)

    assert all(r.passed for r in results), [(r.case_id, r.failure_reason) for r in results if not r.passed]
    assert claims
    assert [(case, created.get(task_id)) for case, task_id in claims if created.get(task_id) != case] == []


# -- regression runner -------------------------------------------------------------


def test_a_group_with_a_missing_path_fails_without_running_pytest(monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("pytest must not start for a group with missing paths")

    monkeypatch.setattr(regression.subprocess, "run", must_not_run)
    result = regression._run_group("regression-demo", "demo", ("tests/agent_tasks", "tests/no_such_suite.py"), TEST_DATABASE_URL)
    assert result.passed is False and not result.skipped
    assert "tests/no_such_suite.py" in (result.failure_reason or "")


@requires_postgres
def test_regression_refuses_when_pytest_would_use_the_application_database(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", _respelled(TEST_DATABASE_URL))
    monkeypatch.setattr(regression, "_run_group", lambda *a: pytest.fail("suites must not run"))
    results = regression.run_regression_evaluation(protected_database_url=TEST_DATABASE_URL)
    assert len(results) == len(regression.GROUPS)
    assert all(r.skipped and r.skip_reason.startswith("REFUSED") for r in results)


def test_regression_has_nothing_to_protect_without_an_application_database():
    assert regression.refusal_reason(None) is None


# -- CLI ---------------------------------------------------------------------------


def _load_cli():
    path = Path(__file__).resolve().parents[2] / "backend" / "scripts" / "run_evaluation.py"
    spec = importlib.util.spec_from_file_location("run_evaluation_cli", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@requires_postgres
@pytest.mark.parametrize("flag", ["--isolated-database-url", "--with-regression"])
def test_cli_exits_2_before_evaluating_when_it_would_touch_the_application_database(monkeypatch, flag):
    cli = _load_cli()
    monkeypatch.setattr(cli, "run_full_suite", lambda **kwargs: pytest.fail("no evaluation may run"))
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    variant = _respelled(TEST_DATABASE_URL)
    if flag == "--with-regression":
        monkeypatch.setenv("TEST_DATABASE_URL", variant)
        argv = ["run_evaluation.py", flag]
    else:
        monkeypatch.delenv("EVALUATION_DATABASE_URL", raising=False)
        argv = ["run_evaluation.py", flag, variant]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 2
