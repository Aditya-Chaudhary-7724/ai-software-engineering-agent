"""Public entry point for the agent, hiding LangGraph's interrupt/resume
mechanics behind a plain run()/resume() pair.

Each conversation needs a stable `thread_id` (any caller-chosen string)
so the checkpointer can resume the exact paused state after a human
approval decision — the same `thread_id` must be passed to both `run`
and the matching `resume`.

Phase 13: `thread_id` doubles as the observability `trace_id` — one
trace per conversation, spanning every `run()`/`resume()` call (human
approval, fix-loop retries) rather than inventing a second ID. Tracing
defaults to a `Tracer(NullRecorder())` (records spans in-process for
correctness, persists nothing) when no `tracer` is supplied, so every
existing call site and test keeps working unchanged.

F6: `resume()` is bound to the exact pending approval. It requires the
pending interrupt's id and the SHA-256 of its diff (both returned on the
`awaiting_approval` result), re-verifies them against the CHECKPOINTED
state before resuming, and refuses non-boolean decisions — raising an
`ApprovalError` subclass without touching the graph, so a refused
decision leaves the approval pending. A per-thread lock makes concurrent
decisions in one process single-use; across processes, the API's
conditional database update (agent_tasks/store.py) is the single-use gate.
`human_approval_node` re-checks the hash again (agent/approval.py).
"""

import hmac
import threading
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from graph.client import Neo4jClient
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.store import VectorStore

from rag.llm.base import LLMProvider

from sandbox.base import TestRunner

from observability.providers import TracedLLMProvider, TracedTestRunner
from observability.recorder import NullRecorder
from observability.tracer import Tracer

from agent.approval import (
    ApprovalInProgressError,
    ApprovalMismatchError,
    InvalidApprovalError,
    NoPendingApprovalError,
    StaleApprovalError,
    hashes_match,
)
from agent.approval import diff_sha256 as compute_diff_sha256
from agent.graph import build_agent_graph
from agent.models import AgentRunResult
from agent.state import initial_state


class AgentService:
    def __init__(
        self,
        vector_store: VectorStore,
        neo4j_client: Neo4jClient,
        embedding_provider: EmbeddingProvider,
        llm_provider: LLMProvider,
        test_runner: TestRunner,
        tracer: Optional[Tracer] = None,
        checkpointer: Optional[BaseCheckpointSaver] = None,
    ) -> None:
        self._tracer = tracer or Tracer(NullRecorder())
        traced_llm_provider = TracedLLMProvider(llm_provider, self._tracer)
        traced_test_runner = TracedTestRunner(test_runner, self._tracer)
        self._graph = build_agent_graph(
            vector_store,
            neo4j_client,
            embedding_provider,
            traced_llm_provider,
            traced_test_runner,
            self._tracer,
            checkpointer=checkpointer,
        )

    def run(self, question: str, repository_id: int, root_path: str, thread_id: str) -> AgentRunResult:
        self._tracer.start_trace(
            thread_id, "agent_thread", attributes={"question": question, "repository_id": repository_id, "root_path": root_path}
        )
        config = {"configurable": {"thread_id": thread_id}}
        with self._tracer.span(thread_id, "agent_run", "agent") as span:
            result = self._graph.invoke(initial_state(question, repository_id, root_path), config=config)
            if result.get("__interrupt__"):
                span.status = "interrupted"
        agent_result = self._to_result(result)
        self._tracer.finish_trace(thread_id, status=agent_result.status)
        return agent_result

    def resume(self, thread_id: str, approved: bool, *, interrupt_id: str, diff_sha256: str) -> AgentRunResult:
        # `type(...) is bool`: rejects 1/0, "yes"/"no", None, {} — and bool
        # subclasses — before anything else happens.
        if type(approved) is not bool or not isinstance(interrupt_id, str) or not interrupt_id:
            raise InvalidApprovalError("approved must be a boolean and interrupt_id a non-empty string")
        config = {"configurable": {"thread_id": thread_id}}
        with _exclusive_decision(thread_id):
            self._verify_pending_approval(config, interrupt_id, diff_sha256)
            decision = {"approved": approved, "diff_sha256": diff_sha256}
            with self._tracer.span(thread_id, "agent_resume", "approval", attributes={"approved": approved}) as span:
                # Keyed by interrupt id: LangGraph delivers the decision to
                # that interrupt only.
                result = self._graph.invoke(Command(resume={interrupt_id: decision}), config=config)
                if result.get("__interrupt__"):
                    span.status = "interrupted"
        agent_result = self._to_result(result)
        self._tracer.finish_trace(thread_id, status=agent_result.status)
        return agent_result

    def pending_approval(self, thread_id: str) -> Optional[AgentRunResult]:
        """The checkpointed pending approval for `thread_id`, or None. Lets a
        process that did not start the thread (e.g. after a restart) see
        exactly what is awaiting a decision."""
        snapshot = self._graph.get_state({"configurable": {"thread_id": thread_id}})
        if not snapshot.interrupts:
            return None
        return self._to_result({**snapshot.values, "__interrupt__": list(snapshot.interrupts)})

    def _verify_pending_approval(self, config: dict, interrupt_id: str, presented_hash: str) -> None:
        snapshot = self._graph.get_state(config)
        pending = list(snapshot.interrupts)
        if not pending:
            raise NoPendingApprovalError("no approval is pending for this thread")
        if not hmac.compare_digest(pending[0].id.encode(), interrupt_id.encode()):
            raise StaleApprovalError("the decision is for an interrupt that is no longer pending")
        # Recomputed from the checkpointed diff — never trusted from the caller.
        if not hashes_match(presented_hash, compute_diff_sha256(snapshot.values.get("proposed_diff"))):
            raise ApprovalMismatchError("the decision does not match the pending diff")

    @staticmethod
    def _to_result(result: dict) -> AgentRunResult:
        pending_interrupts = result.get("__interrupt__")
        message = pending_interrupts[0].value if pending_interrupts else None
        return AgentRunResult(
            status="awaiting_approval" if pending_interrupts else "completed",
            question=result["question"],
            final_response=None if pending_interrupts else result["final_response"],
            sources=[] if pending_interrupts else result["sources"],
            execution_log=result["execution_log"],
            interrupt_message=message,
            interrupt_id=pending_interrupts[0].id if pending_interrupts else None,
            diff_sha256=compute_diff_sha256(message.get("diff")) if message is not None else None,
            task_type=result.get("task_type"),
            approved=result.get("approved"),
            applied=result.get("applied"),
            test_ran=bool(result.get("test_ran")),
            test_passed=result.get("test_passed"),
        )


_decision_locks: Dict[str, threading.Lock] = {}
_decision_locks_guard = threading.Lock()


@contextmanager
def _exclusive_decision(thread_id: str) -> Iterator[None]:
    """Non-blocking: a second concurrent decision for the same thread is
    refused (ApprovalInProgressError) rather than queued behind the first,
    which would otherwise re-validate against a state that is mid-change."""
    with _decision_locks_guard:
        lock = _decision_locks.setdefault(thread_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ApprovalInProgressError("a decision for this thread is already being applied")
    try:
        yield
    finally:
        # Unregister BEFORE releasing. Releasing first let a second caller
        # fetch and acquire this lock, after which this block deleted the
        # entry it was holding, so a third caller created a new lock and ran
        # concurrently with the second. Now, once the entry is gone, a new
        # caller gets a new lock, and the work it guarded is already done.
        with _decision_locks_guard:
            if _decision_locks.get(thread_id) is lock:
                del _decision_locks[thread_id]
        lock.release()
