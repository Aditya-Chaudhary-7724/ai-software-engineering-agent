"""F7 agent-task lifecycle evaluation: the REAL F6 stack — `AgentTaskStore`
(the SQL approval gate), `AgentTaskExecutor`, `BackgroundWorker.run_once`,
`AgentService` and `PostgresSaver` — with deterministic stand-ins only for
the LLM (StubLLMProvider), embeddings (hash), graph (none) and sandbox
(`ScriptedTestRunner`, which never executes anything).

ISOLATION (why this needs its own database): `claim_next` claims the oldest
queued task in the table. Against the application's own DATABASE_URL, a
running API agent worker could claim an evaluation task (or this runner
could claim a real one). So this runner only runs against an explicitly
provided `isolated_database_url`; refuses one that is the SAME database as
DATABASE_URL however the URL is spelled (`evaluation.safety`); and refuses
one that already holds an active (queued/running/awaiting-approval) task it
did not create.

CASE ISOLATION: for the same reason, a case must start with no active task
in the table and leave none behind. A task left queued by one case is the
oldest row the next case's worker claims — which once made the
worker-interruption case run against a leftover task and pass without
testing what it claims. Each case is now checked before and after; a
leftover task fails the case and is terminated.

Every repository is a fresh temporary directory (`evaluation.safety`), so
approving stub output can never touch a real checkout.
"""

import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, List, Optional, Tuple

import psycopg

from agent.approval import diff_sha256
from agent.checkpointing import setup_postgres_checkpointer
from agent_tasks.executor import AgentTaskDependencies, AgentTaskExecutor
from agent_tasks.models import AgentTask
from agent_tasks.store import AgentTaskStore
from jobs.worker import BackgroundWorker
from observability.recorder import NullRecorder
from observability.tracer import Tracer
from rag.llm.stub_provider import StubLLMProvider
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.migrations import apply_schema
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore

from evaluation.cleanup import delete_evaluation_repository
from evaluation.models import CaseResult, MetricResult
from evaluation.safety import UnsafeEvaluationTargetError, application_database_url, require_disposable_root, require_distinct_database
from evaluation.scripted_test_runner import ScriptedTestRunner, make_result

CATEGORY = "agent_lifecycle"
ORIGINAL = "def greet():\n    return 'hi'\n"
INSTRUCTION = "Fix the greet function"
_CONCURRENCY = 10


class _NoGraphClient:
    def run(self, query: str, **params: Any) -> list:
        return []

    def close(self) -> None:
        pass


def _flag(name: str, ok: bool) -> MetricResult:
    return MetricResult(name=name, value=1.0 if ok else 0.0, expected=1.0, passed=ok)


class _Harness:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.vector_store = VectorStore(database_url)
        self.task_ids: List[str] = []
        self.repository_ids: List[int] = []

    def executor(self, test_results=None) -> Tuple[AgentTaskStore, AgentTaskExecutor]:
        runner = ScriptedTestRunner(test_results if test_results is not None else [make_result(True)] * 3)
        deps = AgentTaskDependencies(
            database_url=self.database_url,
            embedding_provider_factory=lambda: (DeterministicLocalEmbeddingProvider(), "deterministic_local"),
            llm_provider_factory=lambda: (StubLLMProvider(), "stub"),
            neo4j_client_factory=_NoGraphClient,
            test_runner_factory=lambda: runner,
            tracer_factory=lambda: Tracer(NullRecorder()),
        )
        store = AgentTaskStore(self.database_url)
        return store, AgentTaskExecutor(store, deps)

    @contextmanager
    def repository(self) -> Iterator[Tuple[Path, int]]:
        with tempfile.TemporaryDirectory(prefix="eval-lifecycle-") as tmp:
            root = Path(tmp).resolve()
            require_disposable_root(str(root))
            (root / "main.py").write_text(ORIGINAL)
            repository_id = IndexingService(DeterministicLocalEmbeddingProvider(), self.vector_store).index_repository(str(root)).repository_id
            self.repository_ids.append(repository_id)
            yield root, repository_id

    def try_submit(self, store: AgentTaskStore, root: Path, repository_id: int) -> Optional[AgentTask]:
        task = store.create(repository_id=repository_id, target_path=str(root), instruction=INSTRUCTION, route="modify")
        if task is not None:
            self.task_ids.append(task.id)
        return task

    def submit(self, store: AgentTaskStore, root: Path, repository_id: int) -> AgentTask:
        task = self.try_submit(store, root, repository_id)
        if task is None:
            raise LookupError("repository unexpectedly has an active task")
        return task

    def sql(self, statement: str, params: tuple = ()) -> None:
        conn = psycopg.connect(self.database_url)
        try:
            with conn.cursor() as cur:
                cur.execute(statement, params)
            conn.commit()
        finally:
            conn.close()

    def terminate_active(self) -> int:
        """Ends every task this harness created that is still active.
        Returns how many there were (0 for a well-behaved case)."""
        conn = psycopg.connect(self.database_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE agent_tasks SET status = 'failed', error_code = 'evaluation_cleanup', approval_expires_at = NULL "
                    "WHERE id = ANY(%s) AND status IN ('queued', 'running', 'awaiting_approval') RETURNING id",
                    (self.task_ids,),
                )
                count = len(cur.fetchall())
            conn.commit()
            return count
        finally:
            conn.close()

    def checkpoint_exists(self, task_id: str) -> bool:
        return _count(self.database_url, "SELECT count(*) FROM checkpoints WHERE thread_id = %s", (task_id,)) > 0

    def cleanup(self) -> None:
        conn = psycopg.connect(self.database_url)
        try:
            with conn.cursor() as cur:
                for task_id in self.task_ids:
                    cur.execute("DELETE FROM agent_tasks WHERE id = %s", (task_id,))
                    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                        cur.execute(f"DELETE FROM {table} WHERE thread_id = %s", (task_id,))
            conn.commit()
        finally:
            conn.close()
        for repository_id in self.repository_ids:
            delete_evaluation_repository(self.vector_store, repository_id)


def _get(store: AgentTaskStore, task_id: str) -> AgentTask:
    task = store.get(task_id)
    if task is None:
        raise LookupError(f"agent task {task_id} disappeared")
    return task


def _count(database_url: str, sql: str, params: tuple = ()) -> int:
    conn = psycopg.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return int(row[0]) if row else 0
    finally:
        conn.close()


def _run_once(store: AgentTaskStore, executor: AgentTaskExecutor) -> bool:
    return BackgroundWorker(store, executor, name="eval-agent", recover=executor.recover_stale).run_once()


def _decide(store: AgentTaskStore, task_id: str, approved: bool, **override: str) -> Optional[AgentTask]:
    current = _get(store, task_id)
    return store.record_decision(
        task_id,
        approved=approved,
        interrupt_id=override.get("interrupt_id", current.proposal_interrupt_id or ""),
        diff_sha256=override.get("diff_sha256", current.proposal_diff_sha256 or ""),
    )


# -- cases -------------------------------------------------------------------


def _case_approve(h: _Harness) -> List[MetricResult]:
    store, executor = h.executor()
    with h.repository() as (root, repository_id):
        task = h.submit(store, root, repository_id)
        _run_once(store, executor)
        paused = _get(store, task.id)
        untouched_while_pending = (root / "main.py").read_text() == ORIGINAL
        hash_binds_diff = paused.proposal_diff_sha256 == diff_sha256(paused.proposal_diff)
        accepted = _decide(store, task.id, True) is not None
        _run_once(store, executor)
        done = _get(store, task.id)
        return [
            _flag("pauses_awaiting_approval", paused.status == "awaiting_approval"),
            _flag("nothing_written_before_approval", untouched_while_pending),
            _flag("proposal_hash_binds_shown_diff", hash_binds_diff),
            _flag("approval_accepted_once", accepted),
            _flag("completed_with_change_applied", done.status == "completed" and done.outcome.get("changes_applied") == 1),
            _flag("file_changed_only_after_approval", (root / "main.py").read_text() != ORIGINAL),
            _flag("checkpoint_deleted_when_terminal", not h.checkpoint_exists(task.id)),
        ]


def _case_reject(h: _Harness) -> List[MetricResult]:
    store, executor = h.executor()
    with h.repository() as (root, repository_id):
        task = h.submit(store, root, repository_id)
        _run_once(store, executor)
        paused = _get(store, task.id)
        _decide(store, task.id, False)
        _run_once(store, executor)
        done = _get(store, task.id)
        approve_after_reject = store.record_decision(
            task.id, approved=True, interrupt_id=paused.proposal_interrupt_id or "", diff_sha256=paused.proposal_diff_sha256 or ""
        )
        return [
            _flag("rejected_status", done.status == "rejected"),
            _flag("approval_after_rejection_refused", approve_after_reject is None),
            _flag("no_change_written", (root / "main.py").read_text() == ORIGINAL),
            _flag("outcome_reports_zero_changes", done.outcome.get("changes_applied") == 0),
        ]


def _case_expiry(h: _Harness) -> List[MetricResult]:
    store, executor = h.executor()
    with h.repository() as (root, repository_id):
        task = h.submit(store, root, repository_id)
        _run_once(store, executor)
        h.sql("UPDATE agent_tasks SET approval_expires_at = now() - interval '1 second' WHERE id = %s", (task.id,))
        refused = _decide(store, task.id, True) is None
        executor.recover_stale(120)
        done = _get(store, task.id)
        return [
            _flag("decision_after_expiry_refused", refused),
            _flag("expired_status", done.status == "expired" and done.error_code == "approval_expired"),
            _flag("no_change_written", (root / "main.py").read_text() == ORIGINAL),
            _flag("checkpoint_deleted", not h.checkpoint_exists(task.id)),
        ]


def _case_stale_reused_mismatched(h: _Harness) -> List[MetricResult]:
    store, executor = h.executor([make_result(False, stderr="boom")] * 3)
    with h.repository() as (root, repository_id):
        task = h.submit(store, root, repository_id)
        _run_once(store, executor)
        first = _get(store, task.id)
        mismatched = _decide(store, task.id, True, diff_sha256=diff_sha256((first.proposal_diff or "") + "+tampered\n")) is None
        wrong_interrupt = _decide(store, task.id, True, interrupt_id="f" * 32) is None
        accepted = _decide(store, task.id, True) is not None
        reused_before_worker = _decide(store, task.id, True) is None
        _run_once(store, executor)  # applies, tests fail -> a NEW fix proposal is pending
        second = _get(store, task.id)
        stale = (
            store.record_decision(
                task.id, approved=True, interrupt_id=first.proposal_interrupt_id or "", diff_sha256=first.proposal_diff_sha256 or ""
            )
            is None
        )
        applied_content = (root / "main.py").read_text()
        _decide(store, task.id, False)  # decline the fix attempt: ends the task
        _run_once(store, executor)
        done = _get(store, task.id)
        return [
            _flag("mismatched_hash_refused", mismatched),
            _flag("unknown_interrupt_refused", wrong_interrupt),
            _flag("valid_decision_accepted", accepted),
            _flag("reused_decision_refused", reused_before_worker),
            _flag("fix_attempt_gets_new_interrupt", second.status == "awaiting_approval" and second.proposal_interrupt_id != first.proposal_interrupt_id),
            _flag("stale_earlier_binding_refused", stale),
            _flag("declined_fix_ends_task_without_writing", done.status == "rejected" and (root / "main.py").read_text() == applied_content),
        ]


def _concurrently(fn: Callable[[int], Any], count: int) -> List[Any]:
    barrier = threading.Barrier(count)
    results: List[Any] = [None] * count

    def worker(i: int) -> None:
        barrier.wait()
        results[i] = fn(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return results


def _case_concurrent_decisions(h: _Harness) -> List[MetricResult]:
    store, executor = h.executor()
    with h.repository() as (root, repository_id):
        task = h.submit(store, root, repository_id)
        _run_once(store, executor)
        pending = _get(store, task.id)
        outcomes = _concurrently(
            lambda i: AgentTaskStore(h.database_url).record_decision(
                task.id, approved=i % 2 == 0, interrupt_id=pending.proposal_interrupt_id or "", diff_sha256=pending.proposal_diff_sha256 or ""
            ),
            _CONCURRENCY,
        )
        accepted = [o for o in outcomes if o is not None]
        # Process the one accepted decision, so the task ends here instead of
        # staying queued for the next case's worker to claim.
        processed = _run_once(store, executor)
        done = _get(store, task.id)
        approved = len(accepted) == 1 and accepted[0].decision_approved is True
        expected_status = "completed" if approved else "rejected"
        file_changed = (root / "main.py").read_text() != ORIGINAL
        return [
            MetricResult(name="decisions_submitted", value=float(_CONCURRENCY)),
            MetricResult(name="decisions_accepted", value=float(len(accepted)), expected=1.0, passed=len(accepted) == 1),
            _flag("accepted_decision_processed", processed and done.status == expected_status),
            _flag("file_matches_the_accepted_decision", file_changed == approved),
        ]


def _case_concurrent_submissions(h: _Harness) -> List[MetricResult]:
    store, _ = h.executor()
    with h.repository() as (root, repository_id):
        created = _concurrently(lambda i: h.try_submit(AgentTaskStore(h.database_url), root, repository_id), _CONCURRENCY)
        winners = [t for t in created if t is not None]
        active = _count(
            h.database_url,
            "SELECT count(*) FROM agent_tasks WHERE repository_id = %s AND status IN ('queued','running','awaiting_approval')",
            (repository_id,),
        )
        for task in winners:  # leave nothing queued: claim_next is table-wide
            store.fail(task.id, "evaluation_cleanup")
        return [
            MetricResult(name="submissions", value=float(_CONCURRENCY)),
            MetricResult(name="tasks_created", value=float(len(winners)), expected=1.0, passed=len(winners) == 1),
            MetricResult(name="active_tasks_for_repository", value=float(active), expected=1.0, passed=active == 1),
        ]


def _case_interruption(h: _Harness) -> List[MetricResult]:
    """A worker that dies AFTER the approval was recorded but BEFORE the
    change was applied: the task must end `interrupted` — never `completed`,
    never silently resumed — and say nothing was applied."""
    store, executor = h.executor()
    with h.repository() as (root, repository_id):
        task = h.submit(store, root, repository_id)
        _run_once(store, executor)
        proposed = _get(store, task.id).status == "awaiting_approval"
        approval_recorded = _decide(store, task.id, True) is not None
        claimed = store.claim_next("eval-worker-that-died")
        h.sql("UPDATE agent_tasks SET heartbeat_at = now() - interval '10 minutes' WHERE id = %s", (task.id,))
        recovered = executor.recover_stale(120)
        late_write_refused = store.finish(task.id, status="completed", final_response="x", sources=[], execution_log=[], outcome={}) is None
        decision_refused = _decide(store, task.id, True) is None
        ran_again = _run_once(store, executor)
        done = _get(store, task.id)
        return [
            _flag("change_was_proposed", proposed),
            _flag("approval_recorded_before_worker_died", approval_recorded),
            _flag("dying_worker_claimed_this_task", claimed is not None and claimed.id == task.id),
            _flag("detected_as_interrupted", task.id in recovered and done.status == "interrupted"),
            _flag("error_code_worker_interrupted", done.error_code == "worker_interrupted"),
            _flag("late_completion_write_refused", late_write_refused),
            _flag("not_resumed_automatically", not ran_again and done.status == "interrupted"),
            _flag("decision_after_interruption_refused", decision_refused),
            _flag("no_change_claimed_or_written", done.outcome.get("changes_applied", 0) == 0 and (root / "main.py").read_text() == ORIGINAL),
        ]


def _case_restart(h: _Harness) -> List[MetricResult]:
    store, first = h.executor()
    with h.repository() as (root, repository_id):
        task = h.submit(store, root, repository_id)
        _run_once(store, first)
        del first  # the process that proposed the change is gone
        approval_recorded = _decide(store, task.id, True) is not None
        store_after, second = h.executor()
        _run_once(store_after, second)
        done = _get(store_after, task.id)
        return [
            _flag("approval_recorded_before_restart", approval_recorded),
            _flag("resumed_by_a_fresh_executor", done.status == "completed"),
            _flag("change_applied_after_restart", done.outcome.get("applied") is True),
        ]


CASES: List[Tuple[str, str, Callable[[_Harness], List[MetricResult]]]] = [
    ("lifecycle-approve-apply", "submit -> propose (nothing written) -> bound approval -> applied -> checkpoint removed", _case_approve),
    ("lifecycle-reject", "a rejected proposal ends 'rejected' and writes nothing", _case_reject),
    ("lifecycle-expiry", "an approval window that passes refuses decisions and ends 'expired'", _case_expiry),
    ("lifecycle-stale-reused-mismatched", "mismatched, unknown, reused and stale decisions are all refused", _case_stale_reused_mismatched),
    ("lifecycle-concurrent-decisions", f"{_CONCURRENCY} simultaneous decisions: exactly one accepted", _case_concurrent_decisions),
    ("lifecycle-concurrent-submissions", f"{_CONCURRENCY} simultaneous submissions for one repository: exactly one task", _case_concurrent_submissions),
    ("lifecycle-worker-interruption", "a worker dying after approval is reported 'interrupted', never as a successful resume", _case_interruption),
    ("lifecycle-restart-resume", "an approval recorded before a restart is processed by a fresh executor", _case_restart),
]


def _active_foreign_tasks(database_url: str) -> int:
    return _count(database_url, "SELECT count(*) FROM agent_tasks WHERE status IN ('queued', 'running', 'awaiting_approval')")


def skipped_cases(reason: str) -> List[CaseResult]:
    return [
        CaseResult(case_id=case_id, category=CATEGORY, description=desc, passed=False, skipped=True, skip_reason=reason)
        for case_id, desc, _ in CASES
    ]


_UNSET = object()


def run_agent_lifecycle_evaluation(isolated_database_url: Optional[str], protected_database_url: Any = _UNSET) -> List[CaseResult]:
    """`protected_database_url` defaults to the application database
    ($DATABASE_URL, or the default the suite uses): never touched here."""
    if not isolated_database_url:
        return skipped_cases(
            "needs an isolated database (--isolated-database-url): a running API agent worker on DATABASE_URL "
            "would race this evaluation for queued tasks"
        )
    if protected_database_url is _UNSET:
        protected_database_url = application_database_url()
    try:
        conn = psycopg.connect(isolated_database_url, connect_timeout=5)
    except psycopg.OperationalError:
        return skipped_cases("isolated database not reachable (REQUIRES EXTERNAL SERVICE)")
    conn.close()
    try:
        require_distinct_database(isolated_database_url, protected_database_url, purpose="agent lifecycle evaluation")
    except UnsafeEvaluationTargetError as exc:
        return skipped_cases(f"REFUSED: {exc}")
    conn = psycopg.connect(isolated_database_url)
    try:
        apply_schema(conn)
    finally:
        conn.close()
    setup_postgres_checkpointer(isolated_database_url)
    foreign = _active_foreign_tasks(isolated_database_url)
    if foreign:
        return skipped_cases(f"isolated database already has {foreign} active agent task(s); refusing to share it")

    harness = _Harness(isolated_database_url)
    results: List[CaseResult] = []
    try:
        for case_id, description, case in CASES:
            start = time.monotonic()
            already_active = _active_foreign_tasks(isolated_database_url)
            if already_active:
                results.append(
                    CaseResult(
                        case_id=case_id, category=CATEGORY, description=description, passed=False,
                        failure_reason=f"{already_active} active task(s) existed before this case; its worker could claim the wrong task",
                    )
                )
                continue
            try:
                try:
                    metrics = case(harness)
                finally:
                    left_active = harness.terminate_active()
                metrics.append(_flag("case_left_no_active_task", left_active == 0))
                failed = [m.name for m in metrics if m.passed is False]
                results.append(
                    CaseResult(
                        case_id=case_id,
                        category=CATEGORY,
                        description=description,
                        passed=not failed,
                        metrics=metrics,
                        latency_seconds=time.monotonic() - start,
                        failure_reason=f"failed: {', '.join(failed)}" if failed else None,
                    )
                )
            except Exception as exc:  # a crash is a failed case, never a dropped one
                results.append(
                    CaseResult(
                        case_id=case_id,
                        category=CATEGORY,
                        description=description,
                        passed=False,
                        latency_seconds=time.monotonic() - start,
                        failure_reason=f"case raised {type(exc).__name__}",
                    )
                )
    finally:
        harness.cleanup()
    return results
