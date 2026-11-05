"""F6: the database approval gate (`AgentTaskStore.record_decision`) and
task lifecycle, against the real test PostgreSQL."""

import threading

import psycopg
import pytest

from agent.approval import diff_sha256
from agent_tasks.store import AgentTaskStore

from tests.agent_tasks.conftest import TEST_DATABASE_URL, requires_postgres

pytestmark = requires_postgres

DIFF = "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-old\n+new\n"
DIGEST = diff_sha256(DIFF)
INTERRUPT = "a" * 32


@pytest.fixture
def store(clean_agent_tables):
    return AgentTaskStore(TEST_DATABASE_URL)


def awaiting_task(store, repository_id=1, ttl=3600, interrupt_id=INTERRUPT, diff=DIFF):
    task = store.create(repository_id=repository_id, target_path=f"/srv/repo-{repository_id}", instruction="fix", route="modify")
    claimed = store.claim_next("worker-test")
    assert claimed.id == task.id
    return store.await_approval(
        task.id,
        interrupt_id=interrupt_id,
        diff=diff,
        diff_sha256=diff_sha256(diff),
        relative_path="main.py",
        message="approve?",
        fix_iteration=0,
        approval_ttl_seconds=ttl,
        execution_log=["Proposed a change."],
        outcome={"generator": "stub"},
    )


def _sql(statement, params=()):
    conn = psycopg.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def test_one_active_task_per_repository(store):
    first = store.create(repository_id=7, target_path="/srv/r", instruction="a", route="answer")
    assert first is not None
    assert store.create(repository_id=7, target_path="/srv/r", instruction="b", route="modify") is None
    assert store.create(repository_id=8, target_path="/srv/s", instruction="b", route="modify") is not None


def test_a_valid_decision_is_accepted_exactly_once(store):
    task = awaiting_task(store)

    accepted = store.record_decision(task.id, approved=True, interrupt_id=INTERRUPT, diff_sha256=DIGEST)
    assert accepted.status == "queued" and accepted.action == "resume" and accepted.decision_approved is True
    assert accepted.approvals_granted == 1

    assert store.record_decision(task.id, approved=True, interrupt_id=INTERRUPT, diff_sha256=DIGEST) is None
    assert store.record_decision(task.id, approved=False, interrupt_id=INTERRUPT, diff_sha256=DIGEST) is None
    assert store.get(task.id).approvals_granted == 1


@pytest.mark.parametrize(
    "interrupt_id, digest",
    [("b" * 32, DIGEST), (INTERRUPT, diff_sha256(DIFF + "x")), (INTERRUPT, DIGEST.upper()), ("", DIGEST)],
    ids=["stale-interrupt", "changed-diff", "case-changed-hash", "empty-interrupt"],
)
def test_mismatched_bindings_match_no_row(store, interrupt_id, digest):
    task = awaiting_task(store)
    assert store.record_decision(task.id, approved=True, interrupt_id=interrupt_id, diff_sha256=digest) is None
    assert store.get(task.id).status == "awaiting_approval"


@pytest.mark.parametrize("approved", [1, 0, "true", None, {}])
def test_record_decision_requires_a_real_boolean(store, approved):
    task = awaiting_task(store)
    with pytest.raises(TypeError):
        store.record_decision(task.id, approved=approved, interrupt_id=INTERRUPT, diff_sha256=DIGEST)
    assert store.get(task.id).status == "awaiting_approval"


def test_an_expired_approval_cannot_be_decided_and_is_expired_by_recovery(store):
    task = awaiting_task(store)
    _sql("UPDATE agent_tasks SET approval_expires_at = now() - interval '1 second' WHERE id = %s", (task.id,))

    assert store.record_decision(task.id, approved=True, interrupt_id=INTERRUPT, diff_sha256=DIGEST) is None
    assert store.recover_stale(120) == [task.id]
    expired = store.get(task.id)
    assert expired.status == "expired" and expired.error_code == "approval_expired"


def test_a_decision_after_a_new_proposal_replaced_the_old_one_is_stale(store):
    """Fix-loop shape: the first proposal is approved and a NEW one is
    pending. The first proposal's binding must not decide the new one."""
    task = awaiting_task(store)
    assert store.record_decision(task.id, approved=True, interrupt_id=INTERRUPT, diff_sha256=DIGEST)
    store.claim_next("worker-test")
    new_diff = DIFF + "+more\n"
    store.await_approval(
        task.id, interrupt_id="c" * 32, diff=new_diff, diff_sha256=diff_sha256(new_diff), relative_path="main.py",
        message="fix 1", fix_iteration=1, approval_ttl_seconds=3600, execution_log=[], outcome={},
    )

    assert store.record_decision(task.id, approved=True, interrupt_id=INTERRUPT, diff_sha256=DIGEST) is None
    assert store.get(task.id).decision_approved is None  # reset for the new proposal


def test_concurrent_decisions_exactly_one_wins(store):
    """20 threads, each with its own database connection, submit the same
    valid binding (some approve, some reject) at the same moment."""
    task = awaiting_task(store)
    barrier = threading.Barrier(20)
    accepted = []

    def decide(i):
        own_store = AgentTaskStore(TEST_DATABASE_URL)
        barrier.wait()
        result = own_store.record_decision(task.id, approved=i % 2 == 0, interrupt_id=INTERRUPT, diff_sha256=DIGEST)
        if result is not None:
            accepted.append(result)

    threads = [threading.Thread(target=decide, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert len(accepted) == 1
    final = store.get(task.id)
    assert final.status == "queued" and final.decision_approved == accepted[0].decision_approved


def test_a_running_task_with_a_stale_heartbeat_is_interrupted(store):
    task = store.create(repository_id=3, target_path="/srv/r3", instruction="fix", route="modify")
    store.claim_next("dead-worker")
    _sql("UPDATE agent_tasks SET heartbeat_at = now() - interval '10 minutes' WHERE id = %s", (task.id,))

    assert store.recover_stale(120) == [task.id]
    interrupted = store.get(task.id)
    assert interrupted.status == "interrupted" and interrupted.error_code == "worker_interrupted"
    # A late write from the dead worker cannot resurrect it.
    assert store.finish(task.id, status="completed", final_response="x", sources=[], execution_log=[], outcome={}) is None
    assert store.get(task.id).status == "interrupted"


def test_a_fresh_heartbeat_is_not_interrupted(store):
    task = store.create(repository_id=4, target_path="/srv/r4", instruction="fix", route="modify")
    store.claim_next("live-worker")
    assert store.recover_stale(120) == []
    assert store.get(task.id).status == "running"
