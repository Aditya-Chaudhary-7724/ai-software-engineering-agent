"""F6 regression tests: human approval is strict, bound to the exact diff,
single-use, and cannot be bypassed — at every layer below HTTP:

- `human_approval_node` (driving the LangGraph graph DIRECTLY, i.e. what a
  caller that bypasses `AgentService` would do);
- `AgentService.resume` (the service API);
- `ModificationService.apply_change` (the only code that writes files).

Real PostgreSQL + real PostgresSaver + real graph; stub LLM; no Neo4j.
"""

import threading
import time

import pytest
from langgraph.types import Command

from agent.approval import (
    ApprovalInProgressError,
    ApprovalMismatchError,
    InvalidApprovalError,
    NoPendingApprovalError,
    StaleApprovalError,
    diff_sha256,
    is_bound_approval,
)
from modification.exceptions import UnauthorizedChangeError
from modification.models import ProposedChange
from modification.service import ModificationService
from rag.llm.stub_provider import StubLLMProvider
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider

from tests.agent_tasks.conftest import (
    MODIFY_INSTRUCTION,
    ORIGINAL_CONTENT,
    FakeTestRunner,
    make_service,
    make_test_result,
    new_thread_id,
    requires_postgres,
)

NON_BOOLEAN_DECISIONS = ["no", "yes", "false", "true", 1, 0, {}, [], [0], None, 1.0]


def _pause(service, repo):
    thread_id = new_thread_id()
    result = service.run(MODIFY_INSTRUCTION, repo["repository_id"], repo["root_path"], thread_id)
    assert result.status == "awaiting_approval"
    return thread_id, result


# -- pure binding rule --------------------------------------------------------


def test_is_bound_approval_accepts_only_true_with_the_matching_hash():
    good = diff_sha256("--- a\n+++ b\n")
    assert is_bound_approval({"approved": True, "diff_sha256": good}, good)
    for decision in NON_BOOLEAN_DECISIONS + [True, False]:
        assert not is_bound_approval(decision, good)
    for approved in ["true", 1, "yes", None]:
        assert not is_bound_approval({"approved": approved, "diff_sha256": good}, good)
    assert not is_bound_approval({"approved": True, "diff_sha256": diff_sha256("other")}, good)
    assert not is_bound_approval({"approved": True, "diff_sha256": good.upper()}, good)
    assert not is_bound_approval({"approved": True}, good)


# -- graph-level bypass -------------------------------------------------------


@requires_postgres
@pytest.mark.parametrize(
    "raw_resume",
    NON_BOOLEAN_DECISIONS
    + [True, {"approved": True}, {"approved": "true", "diff_sha256": "0" * 64}, {"approved": True, "diff_sha256": "0" * 64}],
    ids=repr,
)
def test_driving_the_graph_directly_cannot_approve_without_the_bound_hash(
    vector_store, indexed_repo, checkpoint_conn, raw_resume
):
    """Bypasses AgentService entirely and resumes the LangGraph graph with a
    raw value — including plain `True`, which the pre-F6 code accepted.

    Observed LangGraph 0.6.11 behaviour for two values: `resume=None` raises
    inside the library (UnboundLocalError in pregel/_loop.py) and `resume={}`
    is read as an empty resume-map, so the interrupt simply stays pending.
    Neither can approve; AgentService never sends either (it always sends
    `{interrupt_id: decision}`)."""
    service = make_service(vector_store, checkpoint_conn)
    thread_id, _ = _pause(service, indexed_repo)
    config = {"configurable": {"thread_id": thread_id}}

    try:
        final = service._graph.invoke(Command(resume=raw_resume), config=config)
    except Exception:
        final = None

    assert (indexed_repo["root"] / "main.py").read_text() == ORIGINAL_CONTENT
    if final is None or final.get("__interrupt__"):
        assert service.pending_approval(thread_id) is not None  # nothing was decided
    else:
        assert final["approved"] is False
        assert final["applied"] is False


@requires_postgres
def test_driving_the_graph_with_the_correct_binding_is_the_only_way_to_apply(vector_store, indexed_repo, checkpoint_conn):
    service = make_service(vector_store, checkpoint_conn)
    thread_id, paused = _pause(service, indexed_repo)

    decision = {"approved": True, "diff_sha256": paused.diff_sha256}
    final = service._graph.invoke(Command(resume=decision), config={"configurable": {"thread_id": thread_id}})

    assert final["approved"] is True and final["applied"] is True
    assert (indexed_repo["root"] / "main.py").read_text() != ORIGINAL_CONTENT


# -- service API ---------------------------------------------------------------


@requires_postgres
@pytest.mark.parametrize("approved", NON_BOOLEAN_DECISIONS, ids=repr)
def test_resume_refuses_non_boolean_decisions_and_leaves_the_approval_pending(
    vector_store, indexed_repo, checkpoint_conn, approved
):
    service = make_service(vector_store, checkpoint_conn)
    thread_id, paused = _pause(service, indexed_repo)

    with pytest.raises(InvalidApprovalError):
        service.resume(thread_id, approved=approved, **paused.approval_binding())

    assert (indexed_repo["root"] / "main.py").read_text() == ORIGINAL_CONTENT
    still_pending = service.pending_approval(thread_id)
    assert still_pending is not None and still_pending.interrupt_id == paused.interrupt_id


@requires_postgres
@pytest.mark.parametrize("bad_hash", ["0" * 64, "", "not-a-hash", None, 123])
def test_resume_refuses_a_diff_hash_that_does_not_match_the_checkpointed_diff(
    vector_store, indexed_repo, checkpoint_conn, bad_hash
):
    service = make_service(vector_store, checkpoint_conn)
    thread_id, paused = _pause(service, indexed_repo)

    with pytest.raises(ApprovalMismatchError):
        service.resume(thread_id, approved=True, interrupt_id=paused.interrupt_id, diff_sha256=bad_hash)

    assert (indexed_repo["root"] / "main.py").read_text() == ORIGINAL_CONTENT
    assert service.pending_approval(thread_id) is not None
    # The real decision still works afterwards.
    assert service.resume(thread_id, approved=True, **paused.approval_binding()).applied is True


@requires_postgres
def test_resume_hash_is_recomputed_from_the_checkpoint_not_from_the_result(vector_store, indexed_repo, checkpoint_conn):
    """A caller holding the hash of a DIFFERENT diff (e.g. one it edited
    locally) cannot get it approved."""
    service = make_service(vector_store, checkpoint_conn)
    thread_id, paused = _pause(service, indexed_repo)
    tampered = diff_sha256(paused.interrupt_message["diff"] + "\n+import os; os.system('rm -rf /')\n")

    with pytest.raises(ApprovalMismatchError):
        service.resume(thread_id, approved=True, interrupt_id=paused.interrupt_id, diff_sha256=tampered)


@requires_postgres
def test_resume_refuses_an_unknown_interrupt_id(vector_store, indexed_repo, checkpoint_conn):
    service = make_service(vector_store, checkpoint_conn)
    thread_id, paused = _pause(service, indexed_repo)

    with pytest.raises(StaleApprovalError):
        service.resume(thread_id, approved=True, interrupt_id="f" * 32, diff_sha256=paused.diff_sha256)
    with pytest.raises(InvalidApprovalError):
        service.resume(thread_id, approved=True, interrupt_id="", diff_sha256=paused.diff_sha256)


@requires_postgres
def test_a_decision_for_an_earlier_proposal_cannot_approve_the_fix_attempt(vector_store, indexed_repo, checkpoint_conn):
    """Stale approval: after the original change is approved and its tests
    fail, a NEW interrupt (new id, new diff) is pending. Replaying the first
    decision's binding must not approve the fix."""
    runner = FakeTestRunner([make_test_result(passed=False, stderr="boom")] * 3)
    service = make_service(vector_store, checkpoint_conn, runner)
    thread_id, first = _pause(service, indexed_repo)

    second = service.resume(thread_id, approved=True, **first.approval_binding())
    assert second.status == "awaiting_approval"
    assert second.interrupt_id != first.interrupt_id
    applied_content = (indexed_repo["root"] / "main.py").read_text()

    with pytest.raises((StaleApprovalError, ApprovalMismatchError)):
        service.resume(thread_id, approved=True, **first.approval_binding())
    assert (indexed_repo["root"] / "main.py").read_text() == applied_content
    assert runner.call_count == 1


@requires_postgres
def test_a_used_approval_cannot_be_reused(vector_store, indexed_repo, checkpoint_conn):
    service = make_service(vector_store, checkpoint_conn)
    thread_id, paused = _pause(service, indexed_repo)

    done = service.resume(thread_id, approved=False, **paused.approval_binding())
    assert done.status == "completed" and done.approved is False

    with pytest.raises(NoPendingApprovalError):
        service.resume(thread_id, approved=True, **paused.approval_binding())
    assert (indexed_repo["root"] / "main.py").read_text() == ORIGINAL_CONTENT


@requires_postgres
def test_rejected_change_is_never_written(vector_store, indexed_repo, checkpoint_conn):
    service = make_service(vector_store, checkpoint_conn)
    thread_id, paused = _pause(service, indexed_repo)

    result = service.resume(thread_id, approved=False, **paused.approval_binding())

    assert result.approved is False and result.applied is False
    assert "not approved" in result.final_response
    assert (indexed_repo["root"] / "main.py").read_text() == ORIGINAL_CONTENT


class _SlowRunner(FakeTestRunner):
    def run(self, root_path):
        time.sleep(0.5)
        return super().run(root_path)


@requires_postgres
def test_concurrent_decisions_in_one_process_are_single_use(vector_store, indexed_repo):
    """Two threads (each with its OWN service and database connection, like
    two worker threads) present the same valid binding at the same moment.
    Exactly one is applied; the other is refused, and the file is written
    exactly once."""
    from agent.checkpointing import connect_checkpoint_database

    from tests.agent_tasks.conftest import TEST_DATABASE_URL

    conns = [connect_checkpoint_database(TEST_DATABASE_URL) for _ in range(2)]
    runner = _SlowRunner([make_test_result(passed=True)] * 4)
    services = [make_service(vector_store, c, runner) for c in conns]
    thread_id, paused = _pause(services[0], indexed_repo)

    barrier = threading.Barrier(2)
    outcomes = []

    def decide(service):
        barrier.wait()
        try:
            outcomes.append(service.resume(thread_id, approved=True, **paused.approval_binding()).status)
        except (ApprovalInProgressError, NoPendingApprovalError, StaleApprovalError) as exc:
            outcomes.append(type(exc).__name__)

    threads = [threading.Thread(target=decide, args=(s,)) for s in services]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    for c in conns:
        c.close()

    assert len(outcomes) == 2
    assert outcomes.count("completed") == 1
    assert set(outcomes) - {"completed"} <= {"ApprovalInProgressError", "NoPendingApprovalError", "StaleApprovalError"}
    assert runner.call_count == 1  # applied and verified once, never twice


# -- the file-writing boundary -------------------------------------------------


@pytest.mark.parametrize("approved", [1, "yes", "no", [True], {"approved": True}, 1.0], ids=repr)
def test_apply_change_requires_the_boolean_true_itself(tmp_path, approved):
    (tmp_path / "main.py").write_text(ORIGINAL_CONTENT)
    service = ModificationService(None, DeterministicLocalEmbeddingProvider(), StubLLMProvider())  # type: ignore[arg-type]
    proposal = ProposedChange("main.py", ORIGINAL_CONTENT, "changed\n", "diff")

    with pytest.raises(UnauthorizedChangeError):
        service.apply_change(str(tmp_path), proposal, approved=approved)
    assert (tmp_path / "main.py").read_text() == ORIGINAL_CONTENT
