"""Executes claimed agent tasks (frontend phase F6).

Composes the EXISTING agent unchanged: `AgentService` (Phase 7 LangGraph
workflow, Phase 9 propose/approve/apply, Phase 10 sandboxed post-apply
tests, Phase 13 tracing). What this module adds is only what a background
job needs:

- a durable checkpointer: each execution opens its own connection and
  `PostgresSaver`, so a task paused for approval in one process can be
  resumed by another process, or after a restart (agent/checkpointing.py);
- `thread_id` = the task id, so the agent's trace (Phase 13) is the task's
  trace and the approval binding is per task;
- a resume uses the decision the API recorded in the database row — never
  anything sent by a client directly — and `AgentService.resume`
  re-verifies it against the checkpoint before the graph continues;
- stable error codes only; exception detail goes to the server log;
- checkpoint cleanup once a task is terminal: checkpoints hold full file
  contents and test output, which are not kept longer than needed.

Like Ask, graph evidence is optional: without Neo4j the agent runs with a
graph client that returns no rows (vector + keyword retrieval only).
"""

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import psycopg

from agent.approval import ApprovalError
from agent.checkpointing import (
    UnsafeCheckpointDataError,
    connect_checkpoint_database,
    postgres_checkpointer,
    setup_postgres_checkpointer,
)
from agent.models import AgentRunResult
from agent.service import AgentService
from graph.client import Neo4jClient
from graph.exceptions import MissingCredentialsError as Neo4jMissingCredentialsError
from jobs.ingestion import default_embedding_provider
from observability.recorder import JSONFileRecorder
from observability.tracer import Tracer
from rag.llm.base import LLMProvider
from rag.llm.stub_provider import StubLLMProvider
from sandbox.base import TestRunner
from sandbox.docker_runner import DockerTestRunner
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.store import VectorStore

from agent_tasks.models import (
    ACTION_RESUME,
    DEFAULT_APPROVAL_TTL_SECONDS,
    STATUS_COMPLETED,
    STATUS_REJECTED,
    AgentTask,
)
from agent_tasks.store import AgentTaskStore

logger = logging.getLogger("agent_tasks.executor")


class _NoGraphClient:
    """Neo4j stand-in when the graph is not configured/reachable: no rows,
    so graph expansion contributes nothing (same as api/routes/ask.py)."""

    def run(self, query: str, **params: Any) -> list:
        return []

    def close(self) -> None:
        pass


def default_llm_provider() -> Tuple[LLMProvider, str]:
    if os.environ.get("LLM_API_KEY"):
        from rag.llm.anthropic_provider import AnthropicLLMProvider

        return AnthropicLLMProvider(), "anthropic"
    return StubLLMProvider(), "stub"


@dataclass
class AgentTaskDependencies:
    database_url: str
    approval_ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS
    embedding_provider_factory: Callable[[], Tuple[EmbeddingProvider, str]] = default_embedding_provider
    llm_provider_factory: Callable[[], Tuple[LLMProvider, str]] = default_llm_provider
    neo4j_client_factory: Callable[[], Any] = Neo4jClient
    test_runner_factory: Callable[[], TestRunner] = DockerTestRunner
    tracer_factory: Callable[[], Tracer] = lambda: Tracer(JSONFileRecorder())


class AgentTaskExecutor:
    def __init__(self, store: AgentTaskStore, deps: AgentTaskDependencies) -> None:
        self._store = store
        self._deps = deps
        self._checkpointer_ready = False

    def execute(self, task: AgentTask) -> None:
        """Runs one claimed task to its next resting state (awaiting
        approval, or terminal). Never raises."""
        try:
            if not Path(task.target_path).is_dir():
                self._fail(task, "repository_files_unavailable")
                return
            with self._agent_service() as (service, generator):
                if task.action == ACTION_RESUME:
                    result = self._resume(service, task)
                else:
                    result = service.run(task.instruction, task.repository_id, task.target_path, thread_id=task.id)
            self._record(task, result, generator)
        except ApprovalError as exc:
            # The checkpoint no longer matches the decision the API accepted
            # (e.g. checkpoint tables were cleared): refuse, never guess.
            logger.warning("agent task resume refused", extra={"task_id": task.id, "error_code": exc.code})
            self._fail(task, exc.code)
        except UnsafeCheckpointDataError:
            logger.warning("agent task state refused by the checkpoint serializer", extra={"task_id": task.id})
            self._fail(task, "checkpoint_serialization_refused")
        except psycopg.OperationalError:
            logger.warning("agent task lost the database", extra={"task_id": task.id})
            self._fail(task, "database_unavailable")
        except Exception:
            logger.exception("agent task crashed", extra={"task_id": task.id})
            self._fail(task, "internal_error")

    def recover_stale(self, stale_after_seconds: int) -> List[str]:
        """Worker recovery hook: expire/interrupt stale tasks, then delete
        their checkpoints (an expired approval can never be resumed).
        Also creates LangGraph's checkpoint tables on first use; a database
        error propagates to the worker loop, which backs off and retries."""
        if not self._checkpointer_ready:
            setup_postgres_checkpointer(self._deps.database_url)
            self._checkpointer_ready = True
        ids = self._store.recover_stale(stale_after_seconds)
        for task_id in ids:
            self._delete_checkpoint(task_id)
        return ids

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _resume(service: AgentService, task: AgentTask) -> AgentRunResult:
        if task.decision_approved is None or not task.proposal_interrupt_id or not task.proposal_diff_sha256:
            raise ApprovalError("queued resume without a recorded decision")
        return service.resume(
            task.id,
            approved=task.decision_approved,
            interrupt_id=task.proposal_interrupt_id,
            diff_sha256=task.proposal_diff_sha256,
        )

    @contextmanager
    def _agent_service(self) -> Iterator[Tuple[AgentService, str]]:
        embedding_provider, _ = self._deps.embedding_provider_factory()
        llm_provider, generator = self._deps.llm_provider_factory()
        graph_client = self._open_graph()
        conn = connect_checkpoint_database(self._deps.database_url)
        try:
            service = AgentService(
                VectorStore(self._deps.database_url),
                graph_client,
                embedding_provider,
                llm_provider,
                self._deps.test_runner_factory(),
                tracer=self._deps.tracer_factory(),
                checkpointer=postgres_checkpointer(conn),
            )
            yield service, generator
        finally:
            conn.close()
            graph_client.close()

    def _open_graph(self):
        try:
            return self._deps.neo4j_client_factory()
        except Neo4jMissingCredentialsError:
            return _NoGraphClient()
        except Exception:
            logger.warning("neo4j unavailable for agent task; continuing without graph evidence")
            return _NoGraphClient()

    def _record(self, task: AgentTask, result: AgentRunResult, generator: str) -> None:
        # One resume runs apply_change at most once, so an approved resume
        # that ends with `applied` set wrote exactly one change. Counted so a
        # later rejected fix attempt can't hide that an earlier change WAS
        # written (the final state's `applied` only describes the last step).
        wrote_change = task.action == ACTION_RESUME and task.decision_approved is True and result.applied is True
        changes_applied = int(task.outcome.get("changes_applied") or 0) + (1 if wrote_change else 0)
        outcome = {**_outcome(result, generator), "changes_applied": changes_applied}
        if result.status == "awaiting_approval":
            message = result.interrupt_message or {}
            assert result.interrupt_id is not None and result.diff_sha256 is not None
            self._store.await_approval(
                task.id,
                interrupt_id=result.interrupt_id,
                diff=message.get("diff") or "",
                diff_sha256=result.diff_sha256,
                relative_path=message.get("relative_path"),
                message=message.get("message") or "Human approval required.",
                fix_iteration=int(message.get("fix_iteration") or 0),
                approval_ttl_seconds=self._deps.approval_ttl_seconds,
                execution_log=list(result.execution_log),
                outcome=outcome,
            )
            return
        status = STATUS_REJECTED if result.approved is False else STATUS_COMPLETED
        self._store.finish(
            task.id,
            status=status,
            final_response=result.final_response,
            sources=list(result.sources),
            execution_log=list(result.execution_log),
            outcome=outcome,
        )
        self._delete_checkpoint(task.id)

    def _fail(self, task: AgentTask, code: str) -> None:
        try:
            self._store.fail(task.id, code)
        except Exception:
            logger.exception("could not record agent task failure", extra={"task_id": task.id})
        self._delete_checkpoint(task.id)

    def _delete_checkpoint(self, task_id: str) -> None:
        try:
            with connect_checkpoint_database(self._deps.database_url) as conn:
                postgres_checkpointer(conn).delete_thread(task_id)
        except Exception:
            logger.warning("could not delete agent task checkpoint", extra={"task_id": task_id})


def _outcome(result: AgentRunResult, generator: str) -> Dict[str, Optional[Any]]:
    return {
        "generator": generator,
        "approved": result.approved,
        "applied": result.applied,
        "test_ran": result.test_ran,
        "test_passed": result.test_passed,
    }
