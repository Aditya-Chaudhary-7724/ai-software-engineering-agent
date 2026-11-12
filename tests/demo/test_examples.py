"""Pre-generated demo examples: the operator-only generator script (real
retrieval and real agent graph up to the proposal, fake LLM) and the
read-only endpoint the demo uses."""

import importlib.util
import json
from pathlib import Path

import psycopg
import pytest

from api.v1 import API_V1_PREFIX
from rag.llm.base import LLMProvider

from tests.agent_tasks.conftest import ORIGINAL_CONTENT, clean_agent_tables, indexed_repo  # noqa: F401
from tests.api.conftest import TEST_DATABASE_URL, api_client, app, auth_headers, requires_postgres  # noqa: F401
from tests.api.test_demo_scope import DEMO_TOKEN, demo_headers
from tests.vectorstore.conftest import vector_store  # noqa: F401

pytestmark = requires_postgres
SCRIPT = Path(__file__).resolve().parents[2] / "backend" / "scripts" / "generate_demo_examples.py"


def _script():
    spec = importlib.util.spec_from_file_location("generate_demo_examples", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGroq(LLMProvider):
    model = "vendor/operator-model"

    def __init__(self, root="", fail=False):
        self.calls, self.root, self.fail = 0, root, fail

    def generate(self, prompt, system=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        if "Requested change" in prompt:
            return "def greet():\n    \"\"\"Says hi.\"\"\"\n    return 'hi'\n"
        return f"greet returns 'hi' (see {self.root}/main.py)."


def _sql(sql, params=()):
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchall() if cursor.description else None


@pytest.fixture
def env(monkeypatch, indexed_repo, tmp_path):  # noqa: F811
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    _sql("DELETE FROM demo_examples")
    _sql("TRUNCATE demo_rate_events, demo_token_usage")
    spec = tmp_path / "examples.json"
    spec.write_text(json.dumps({"questions": ["What does greet return?", "Where is greet defined?"],
                                "task": "Add a docstring to the greet function"}))
    yield indexed_repo, str(spec)
    _sql("DELETE FROM demo_examples")


def _run(spec, repository_id, *flags, llm=None):
    llm = llm or FakeGroq()
    code = _script().main(["--repository-id", str(repository_id), "--input", spec, *flags], llm_factory=lambda: (llm, "groq"))
    return code, llm


def test_generates_answers_with_citations_and_a_never_applied_task_diff(env):
    repo, spec = env
    before_checkpoints = _sql("SELECT count(*) FROM checkpoints")
    code, llm = _run(spec, repo["repository_id"], "--confirm-real-calls", llm=FakeGroq(root=repo["root_path"]))

    assert code == 0 and llm.calls >= 3
    rows = _sql("SELECT kind, position, prompt, answer, sources, diff, diff_relative_path, generator FROM demo_examples ORDER BY kind, position")
    asks, tasks = [r for r in rows if r[0] == "ask"], [r for r in rows if r[0] == "task"]
    assert [r[2] for r in asks] == ["What does greet return?", "Where is greet defined?"]
    assert all(r[3] and repo["root_path"] not in r[3] for r in asks)  # server path scrubbed
    assert all(s["relative_path"] == "main.py" for r in asks for s in r[4])
    assert len(tasks) == 1 and '+    """Says hi."""' in tasks[0][5] and tasks[0][6] == "main.py"
    assert {r[7] for r in rows} == {"groq:vendor/operator-model"}
    # Nothing applied, nothing queued, nothing persisted, no quota spent:
    assert (repo["root"] / "main.py").read_text() == ORIGINAL_CONTENT
    assert _sql("SELECT count(*) FROM agent_tasks") == [(0,)]
    assert _sql("SELECT count(*) FROM checkpoints") == before_checkpoints
    assert _sql("SELECT count(*) FROM demo_token_usage") == [(0,)]


def test_refuses_real_calls_without_confirmation(env):
    repo, spec = env
    code, llm = _run(spec, repo["repository_id"])
    assert code == 2 and llm.calls == 0 and _sql("SELECT count(*) FROM demo_examples") == [(0,)]


def test_refuses_the_stub_unless_allowed(env):
    repo, spec = env
    from rag.llm.stub_provider import StubLLMProvider

    module = _script()
    assert module.main(["--repository-id", str(repo["repository_id"]), "--input", spec],
                       llm_factory=lambda: (StubLLMProvider(), "stub")) == 2
    assert module.main(["--repository-id", str(repo["repository_id"]), "--input", spec, "--allow-stub"],
                       llm_factory=lambda: (StubLLMProvider(), "stub")) == 0


def test_existing_examples_need_replace_and_a_failure_keeps_the_previous_set(env):
    repo, spec = env
    assert _run(spec, repo["repository_id"], "--confirm-real-calls")[0] == 0
    assert _run(spec, repo["repository_id"], "--confirm-real-calls")[0] == 2  # no --replace
    before = _sql("SELECT count(*) FROM demo_examples")
    code, _ = _run(spec, repo["repository_id"], "--confirm-real-calls", "--replace", llm=FakeGroq(fail=True))
    assert code == 3 and _sql("SELECT count(*) FROM demo_examples") == before


@pytest.mark.parametrize(
    "payload",
    [{"questions": []}, {"questions": ["q"] * 6}, {"questions": ["q"], "task": "What does greet return?"}, {"nope": 1}],
)
def test_invalid_input_is_refused_before_any_call(env, tmp_path, payload):
    repo, _ = env
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(payload))
    code, llm = _run(str(bad), repo["repository_id"], "--confirm-real-calls")
    assert code == 2 and llm.calls == 0


# -- the read endpoint -------------------------------------------------------------------------


def test_the_demo_reads_examples_instantly_and_free(api_client, env, monkeypatch, vector_store, tmp_path):  # noqa: F811
    from tests.api.conftest import insert_repository

    repo, spec = env
    assert _run(spec, repo["repository_id"], "--confirm-real-calls")[0] == 0
    other = insert_repository(vector_store, str(tmp_path / "private"), name="private")
    monkeypatch.setenv("DEMO_API_SERVICE_TOKEN", DEMO_TOKEN)
    monkeypatch.setenv("DEMO_REPOSITORY_IDS", str(repo["repository_id"]))

    body = api_client.get(f"{API_V1_PREFIX}/repositories/{repo['repository_id']}/demo-examples", headers=demo_headers()).json()
    assert len(body["examples"]) == 3 and all(e["pre_generated"] for e in body["examples"])
    assert {e["kind"] for e in body["examples"]} == {"ask", "task"}
    assert _sql("SELECT count(*) FROM demo_rate_events") == [(0,)]  # never counted
    hidden = api_client.get(f"{API_V1_PREFIX}/repositories/{other}/demo-examples", headers=demo_headers())
    assert hidden.status_code == 404
    assert api_client.get(f"{API_V1_PREFIX}/repositories/{repo['repository_id']}/demo-examples", headers=auth_headers()).status_code == 200
