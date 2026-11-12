"""F6: the agent-task executor end to end — real store, real worker loop,
real AgentService + PostgresSaver — including rejection, worker
interruption, and a "restart" between proposal and approval."""

import psycopg

from agent.approval import diff_sha256
from agent.checkpointing import connect_checkpoint_database, postgres_checkpointer
from jobs.worker import BackgroundWorker

from tests.agent_tasks.conftest import (
    MODIFY_INSTRUCTION,
    ORIGINAL_CONTENT,
    TEST_DATABASE_URL,
    FakeTestRunner,
    checkpoint_exists,
    make_executor,
    make_test_result,
    requires_postgres,
)

pytestmark = requires_postgres


def _submit(store, repo, instruction=MODIFY_INSTRUCTION, route="modify"):
    return store.create(
        repository_id=repo["repository_id"], target_path=repo["root_path"], instruction=instruction, route=route
    )


def _run_once(store, executor):
    worker = BackgroundWorker(store, executor, name="agent-test", recover=executor.recover_stale)
    return worker.run_once()


def _decide(store, task, approved):
    current = store.get(task.id)
    return store.record_decision(
        task.id, approved=approved, interrupt_id=current.proposal_interrupt_id, diff_sha256=current.proposal_diff_sha256
    )


def test_modify_task_pauses_with_the_proposed_diff_and_writes_nothing(indexed_repo):
    store, executor = make_executor()
    task = _submit(store, indexed_repo)

    assert _run_once(store, executor)

    paused = store.get(task.id)
    assert paused.status == "awaiting_approval"
    assert paused.proposal_relative_path == "main.py"
    assert paused.proposal_diff and paused.proposal_diff_sha256 == diff_sha256(paused.proposal_diff)
    assert paused.proposal_interrupt_id and paused.approval_expires_at is not None
    assert paused.outcome["generator"] == "stub"
    assert any("awaiting human approval" in e for e in paused.execution_log)
    assert (indexed_repo["root"] / "main.py").read_text() == ORIGINAL_CONTENT
    assert checkpoint_exists(task.id)


def test_approved_task_is_applied_verified_and_its_checkpoint_deleted(indexed_repo):
    runner = FakeTestRunner([make_test_result(passed=True)])
    store, executor = make_executor(runner)
    task = _submit(store, indexed_repo)
    _run_once(store, executor)

    assert _decide(store, task, True) is not None
    assert _run_once(store, executor)

    done = store.get(task.id)
    assert done.status == "completed", done.error_code
    assert done.outcome["applied"] is True and done.outcome["changes_applied"] == 1
    assert done.outcome["test_ran"] is True and done.outcome["test_passed"] is True
    assert (indexed_repo["root"] / "main.py").read_text() != ORIGINAL_CONTENT
    assert runner.call_count == 1
    assert not checkpoint_exists(task.id)


def test_rejected_task_ends_rejected_and_writes_nothing(indexed_repo):
    store, executor = make_executor()
    task = _submit(store, indexed_repo)
    _run_once(store, executor)

    _decide(store, task, False)
    _run_once(store, executor)

    done = store.get(task.id)
    assert done.status == "rejected"
    assert done.outcome["approved"] is False and done.outcome["changes_applied"] == 0
    assert "not approved" in done.final_response
    assert (indexed_repo["root"] / "main.py").read_text() == ORIGINAL_CONTENT


def test_rejecting_a_fix_attempt_still_reports_the_earlier_applied_change(indexed_repo):
    runner = FakeTestRunner([make_test_result(passed=False, stderr="boom")] * 3)
    store, executor = make_executor(runner)
    task = _submit(store, indexed_repo)
    _run_once(store, executor)
    _decide(store, task, True)
    _run_once(store, executor)

    fix = store.get(task.id)
    assert fix.status == "awaiting_approval" and fix.proposal_fix_iteration == 1
    _decide(store, task, False)
    _run_once(store, executor)

    done = store.get(task.id)
    assert done.status == "rejected"
    assert done.outcome["changes_applied"] == 1  # the original approved change WAS written
    assert "remains applied" in done.final_response


def test_approval_survives_an_executor_restart(indexed_repo):
    """The executor that proposed the change is discarded (a restart); a
    brand-new executor, with nothing in memory, processes the approval."""
    store, first = make_executor()
    task = _submit(store, indexed_repo)
    _run_once(store, first)
    del first

    _decide(store, task, True)
    store_after_restart, second = make_executor()
    _run_once(store_after_restart, second)

    done = store_after_restart.get(task.id)
    assert done.status == "completed", done.error_code
    assert done.outcome["applied"] is True


def test_a_decision_whose_checkpoint_is_gone_fails_closed(indexed_repo):
    store, executor = make_executor()
    task = _submit(store, indexed_repo)
    _run_once(store, executor)
    with connect_checkpoint_database(TEST_DATABASE_URL) as conn:
        postgres_checkpointer(conn).delete_thread(task.id)

    _decide(store, task, True)
    _run_once(store, executor)

    done = store.get(task.id)
    assert done.status == "failed" and done.error_code == "approval_not_pending"
    assert (indexed_repo["root"] / "main.py").read_text() == ORIGINAL_CONTENT


def test_an_interrupted_worker_marks_the_task_interrupted_and_drops_its_checkpoint(indexed_repo):
    store, executor = make_executor()
    task = _submit(store, indexed_repo)
    _run_once(store, executor)
    _decide(store, task, True)
    store.claim_next("worker-that-died")  # claimed, then the process "dies"
    conn = psycopg.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("UPDATE agent_tasks SET heartbeat_at = now() - interval '10 minutes' WHERE id = %s", (task.id,))
    conn.commit()
    conn.close()

    assert executor.recover_stale(120) == [task.id]

    done = store.get(task.id)
    assert done.status == "interrupted" and done.error_code == "worker_interrupted"
    assert not checkpoint_exists(task.id)


def test_expired_approval_is_expired_and_cannot_be_resumed(indexed_repo):
    store, executor = make_executor()
    task = _submit(store, indexed_repo)
    _run_once(store, executor)
    conn = psycopg.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("UPDATE agent_tasks SET approval_expires_at = now() - interval '1 second' WHERE id = %s", (task.id,))
    conn.commit()
    conn.close()

    assert _decide(store, task, True) is None
    executor.recover_stale(120)
    assert store.get(task.id).status == "expired"
    assert not checkpoint_exists(task.id)
    assert (indexed_repo["root"] / "main.py").read_text() == ORIGINAL_CONTENT


def test_answer_task_completes_without_any_approval(indexed_repo):
    store, executor = make_executor()
    task = _submit(store, indexed_repo, instruction="What does greet return?", route="answer")
    _run_once(store, executor)

    done = store.get(task.id)
    assert done.status == "completed" and done.proposal_diff is None
    assert done.final_response and "stub-llm" in done.final_response
    assert (indexed_repo["root"] / "main.py").read_text() == ORIGINAL_CONTENT


def test_missing_repository_files_fail_with_a_stable_code(indexed_repo, tmp_path):
    store, executor = make_executor()
    task = store.create(
        repository_id=indexed_repo["repository_id"], target_path=str(tmp_path / "gone"), instruction="fix it", route="modify"
    )
    _run_once(store, executor)
    done = store.get(task.id)
    assert done.status == "failed" and done.error_code == "repository_files_unavailable"


def test_an_llm_quota_error_fails_the_task_with_its_own_code_and_writes_nothing(indexed_repo):
    from rag.llm.exceptions import LLMQuotaExceededError
    from rag.llm.stub_provider import StubLLMProvider

    class QuotaLLM(StubLLMProvider):
        def generate(self, prompt, system=None):
            raise LLMQuotaExceededError("429", retry_after_seconds=5)

    store, executor = make_executor()
    executor._deps.llm_provider_factory = lambda: (QuotaLLM(), "groq")
    task = _submit(store, indexed_repo)

    assert _run_once(store, executor)

    failed = store.get(task.id)
    assert (failed.status, failed.error_code) == ("failed", "llm_quota_exceeded")
    assert (indexed_repo["root"] / "main.py").read_text() == ORIGINAL_CONTENT
    assert not checkpoint_exists(task.id)


def test_a_misconfigured_llm_fails_the_task_as_not_configured(indexed_repo, monkeypatch):
    from rag.llm.factory import configured_llm_provider

    monkeypatch.setenv("LLM_PROVIDER", "groq")  # no key, no model
    store, executor = make_executor()
    executor._deps.llm_provider_factory = configured_llm_provider
    task = _submit(store, indexed_repo)

    assert _run_once(store, executor)

    assert (store.get(task.id).status, store.get(task.id).error_code) == ("failed", "llm_not_configured")


def test_a_repository_indexed_with_another_embedding_model_fails_as_reindex_required(indexed_repo):
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        conn.execute("UPDATE repositories SET embedding_model = 'openai' WHERE id = %s", (indexed_repo["repository_id"],))
    store, executor = make_executor()
    task = _submit(store, indexed_repo)

    assert _run_once(store, executor)

    failed = store.get(task.id)
    assert (failed.status, failed.error_code) == ("failed", "reindex_required")
    assert (indexed_repo["root"] / "main.py").read_text() == ORIGINAL_CONTENT
