"""Propose-only public-demo agent tasks, end to end: API submission (with
limits and an atomic token reservation), the real worker, executor and
LangGraph PostgresSaver, the per-task token ceiling, and exactly-once
settlement of the day's budget to the real usage. Only the LLM is faked;
it reports usage the way a real provider does."""

import psycopg
import pytest

from agent_tasks.store import AgentTaskStore
from api.v1 import API_V1_PREFIX
from demo.limits import DemoLimiter, DemoLimits
from jobs.worker import BackgroundWorker
from rag.llm.base import LLMProvider
from rag.llm.openai_compatible_provider import LLMUsage

from tests.agent_tasks.conftest import (  # noqa: F401
    MODIFY_INSTRUCTION,
    ORIGINAL_CONTENT,
    checkpoint_exists,
    clean_agent_tables,
    indexed_repo,
    make_executor,
)
from tests.api.conftest import TEST_DATABASE_URL, auth_headers, requires_postgres
from tests.api.test_demo_scope import DEMO_TOKEN, demo_headers
from tests.api.test_v1_agent_tasks import RecordingWorker
from tests.vectorstore.conftest import vector_store  # noqa: F401

pytestmark = requires_postgres
TASKS = f"{API_V1_PREFIX}/agent-tasks"
VISITOR = {"X-Demo-Client-IP": "203.0.113.7"}


class UsageReportingLLM(LLMProvider):
    """Returns valid new file content and reports `tokens` per call."""

    def __init__(self, tokens=900, fail_after_reporting=False):
        self.tokens, self.fail_after_reporting, self.calls, self.last_usage = tokens, fail_after_reporting, 0, None

    def generate(self, prompt, system=None):
        self.calls += 1
        self.last_usage = LLMUsage(prompt_tokens=self.tokens - 100, completion_tokens=100, total_tokens=self.tokens)
        if self.fail_after_reporting:
            raise RuntimeError("provider failed after reporting usage")
        return "def greet():\n    return 'hello'\n"


def _sql(sql, params=()):
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        cursor = conn.execute(sql, params)
        return cursor.fetchall() if cursor.description else None


def _limits(**overrides):
    base = dict(ask_per_ip_per_hour=100, ask_global_per_minute=100, daily_token_budget=100_000, ask_token_reserve=5_000,
                tasks_per_day=5, tasks_per_ip_per_day=2, task_token_reserve=8_000, task_max_tokens=12_000)
    base.update(overrides)
    return DemoLimits(**base)


@pytest.fixture
def demo(app, api_client, monkeypatch, indexed_repo):  # noqa: F811
    for key, value in {
        "DATABASE_URL": TEST_DATABASE_URL, "DEMO_API_SERVICE_TOKEN": DEMO_TOKEN,
        "DEMO_REPOSITORY_IDS": str(indexed_repo["repository_id"]),
        "DEMO_TASKS_PER_DAY": "5", "DEMO_TASKS_PER_IP_PER_DAY": "2",
        "DEMO_TASK_TOKEN_RESERVE": "8000", "DEMO_TASK_MAX_TOKENS": "12000", "DEMO_DAILY_TOKEN_BUDGET": "100000",
    }.items():
        monkeypatch.setenv(key, value)
    app.state.agent_worker = RecordingWorker()
    _sql("TRUNCATE demo_rate_events, demo_token_usage")
    yield indexed_repo
    _sql("TRUNCATE demo_rate_events, demo_token_usage")


def _executor(llm, **limit_overrides):
    store, executor = make_executor()
    executor._deps.demo_llm_provider_factory = lambda: (llm, "groq")
    executor._deps.demo_limits_factory = lambda: _limits(**limit_overrides)
    return store, executor


def _submit(client, repository_id, headers=None, instruction=MODIFY_INSTRUCTION):
    return client.post(
        f"{API_V1_PREFIX}/repositories/{repository_id}/agent-tasks",
        headers=headers or {**demo_headers(), **VISITOR}, json={"instruction": instruction},
    )


def _run(store, executor):
    return BackgroundWorker(store, executor, name="demo-test", recover=executor.recover_stale).run_once()


def _usage():
    return _sql("SELECT tokens, requests FROM demo_token_usage")


# -- the happy path: proposed, display-only, settled -------------------------------------


def test_a_demo_task_ends_at_a_display_only_proposal_and_is_settled_to_real_usage(api_client, demo):
    submitted = _submit(api_client, demo["repository_id"])
    assert submitted.status_code == 202, submitted.text
    body = submitted.json()
    assert body["proposal_only"] is True
    assert _usage() == [(8_000, 1)]  # reserved at submission

    llm = UsageReportingLLM(tokens=900)
    store, executor = _executor(llm)
    assert _run(store, executor)

    task = api_client.get(f"{TASKS}/{body['id']}", headers=demo_headers()).json()
    assert task["status"] == "proposed"
    assert task["proposal"]["state"] == "display_only" and task["proposal"]["interrupt_id"] is None
    assert "+    return 'hello'" in task["proposal"]["diff"]
    assert (demo["root"] / "main.py").read_text() == ORIGINAL_CONTENT  # nothing written
    assert not checkpoint_exists(body["id"])  # nothing to resume
    assert llm.calls >= 1
    assert _usage() == [(900 * llm.calls, 1)]  # settled to the real total
    assert _sql("SELECT demo_used_tokens, demo_settled FROM agent_tasks WHERE id = %s", (body["id"],)) == [
        (900 * llm.calls, True)
    ]


# -- never approvable ------------------------------------------------------------------


def test_an_operator_decision_on_a_demo_proposal_is_refused(api_client, demo):
    task_id = _submit(api_client, demo["repository_id"]).json()["id"]
    store, executor = _executor(UsageReportingLLM())
    _run(store, executor)
    proposed = store.get(task_id)

    response = api_client.post(
        f"{TASKS}/{task_id}/decision", headers=auth_headers(),
        json={"approved": True, "interrupt_id": "ab" * 16, "diff_sha256": proposed.proposal_diff_sha256},
    )

    assert (response.status_code, response.json()) == (409, {"error": "demo_proposal_not_approvable"})
    assert store.get(task_id).status == "proposed"
    assert (demo["root"] / "main.py").read_text() == ORIGINAL_CONTENT


def test_the_store_refuses_even_a_forged_pending_state_on_a_proposal_only_task(api_client, demo):
    task_id = _submit(api_client, demo["repository_id"]).json()["id"]
    store, executor = _executor(UsageReportingLLM())
    _run(store, executor)
    digest = store.get(task_id).proposal_diff_sha256
    # Forge everything a decision needs, directly in the database:
    _sql(
        "UPDATE agent_tasks SET status = 'awaiting_approval', proposal_interrupt_id = 'ab', "
        "approval_expires_at = now() + interval '1 hour' WHERE id = %s",
        (task_id,),
    )
    assert store.record_decision(task_id, approved=True, interrupt_id="ab", diff_sha256=digest) is None


# -- the per-task ceiling ------------------------------------------------------------------


def test_a_task_whose_first_call_could_exceed_the_ceiling_stops_before_calling(api_client, demo):
    task_id = _submit(api_client, demo["repository_id"]).json()["id"]
    llm = UsageReportingLLM()
    store, executor = _executor(llm, task_max_tokens=100)  # below any prompt + completion allowance

    assert _run(store, executor)

    task = store.get(task_id)
    assert (task.status, task.error_code) == ("failed", "demo_task_token_limit_reached")
    assert task.proposal_diff is None  # never a partial diff
    assert llm.calls == 0
    assert _usage() == [(0, 1)]  # reservation released: nothing was used


def test_a_task_failing_midway_is_settled_to_what_it_used(api_client, demo):
    task_id = _submit(api_client, demo["repository_id"]).json()["id"]
    llm = UsageReportingLLM(tokens=1_234, fail_after_reporting=True)
    store, executor = _executor(llm)

    assert _run(store, executor)

    task = store.get(task_id)
    assert task.status == "failed" and task.proposal_diff is None
    assert _usage() == [(1_234, 1)]
    assert task.demo_used_tokens == 1_234 and task.demo_settled


def test_settlement_happens_exactly_once(api_client, demo):
    task_id = _submit(api_client, demo["repository_id"]).json()["id"]
    store, executor = _executor(UsageReportingLLM(tokens=700))
    _run(store, executor)
    before = _usage()
    assert DemoLimiter(TEST_DATABASE_URL, _limits()).settle_task(task_id, 99_999) is False
    assert _usage() == before


# -- limits and atomic submission ------------------------------------------------------------


def _finish_all(store):
    _sql("UPDATE agent_tasks SET status = 'failed' WHERE status IN ('queued', 'running', 'awaiting_approval')")


def test_per_visitor_and_global_daily_task_limits(api_client, demo, monkeypatch):
    monkeypatch.setenv("DEMO_TASKS_PER_DAY", "3")
    store = AgentTaskStore(TEST_DATABASE_URL)
    for _ in range(2):
        assert _submit(api_client, demo["repository_id"]).status_code == 202
        _finish_all(store)
    third = _submit(api_client, demo["repository_id"])
    assert third.status_code == 429 and third.json()["error"] == "demo_daily_limit_reached"
    assert 0 < third.json()["retry_after_seconds"] <= 86_401

    other = {**demo_headers(), "X-Demo-Client-IP": "198.51.100.9"}
    assert _submit(api_client, demo["repository_id"], headers=other).status_code == 202
    _finish_all(store)
    assert _submit(api_client, demo["repository_id"], headers={**demo_headers(), "X-Demo-Client-IP": "192.0.2.4"}).status_code == 429


def test_a_refused_submission_records_and_charges_nothing(api_client, demo):
    # The operator already has an active task on this repository.
    assert _submit(api_client, demo["repository_id"], headers=auth_headers()).status_code == 202
    response = _submit(api_client, demo["repository_id"])
    assert (response.status_code, response.json()) == (409, {"error": "agent_task_active"})
    assert _sql("SELECT count(*) FROM demo_rate_events") == [(0,)]
    assert _usage() == []


def test_a_demo_task_needs_the_visitor_ip(api_client, demo):
    response = _submit(api_client, demo["repository_id"], headers=demo_headers())
    assert (response.status_code, response.json()) == (400, {"error": "demo_client_ip_required"})


# -- visibility ---------------------------------------------------------------------------------


def test_the_demo_sees_only_demo_tasks(api_client, demo):
    operator_task = _submit(api_client, demo["repository_id"], headers=auth_headers()).json()["id"]
    assert api_client.get(f"{TASKS}/{operator_task}", headers=demo_headers()).status_code == 404
    assert api_client.get(f"{TASKS}/{operator_task}", headers=auth_headers()).status_code == 200
    _finish_all(None)
    demo_task = _submit(api_client, demo["repository_id"]).json()["id"]
    assert api_client.get(f"{TASKS}/{demo_task}", headers=demo_headers()).status_code == 200
