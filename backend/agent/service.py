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
"""

from typing import Optional

from langgraph.types import Command

from graph.client import Neo4jClient
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.store import VectorStore

from rag.llm.base import LLMProvider

from sandbox.base import TestRunner

from observability.providers import TracedLLMProvider, TracedTestRunner
from observability.recorder import NullRecorder
from observability.tracer import Tracer

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
    ) -> None:
        self._tracer = tracer or Tracer(NullRecorder())
        traced_llm_provider = TracedLLMProvider(llm_provider, self._tracer)
        traced_test_runner = TracedTestRunner(test_runner, self._tracer)
        self._graph = build_agent_graph(
            vector_store, neo4j_client, embedding_provider, traced_llm_provider, traced_test_runner, self._tracer
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

    def resume(self, thread_id: str, approved: bool) -> AgentRunResult:
        config = {"configurable": {"thread_id": thread_id}}
        with self._tracer.span(thread_id, "agent_resume", "approval", attributes={"approved": approved}) as span:
            result = self._graph.invoke(Command(resume=approved), config=config)
            if result.get("__interrupt__"):
                span.status = "interrupted"
        agent_result = self._to_result(result)
        self._tracer.finish_trace(thread_id, status=agent_result.status)
        return agent_result

    @staticmethod
    def _to_result(result: dict) -> AgentRunResult:
        pending_interrupts = result.get("__interrupt__")
        if pending_interrupts:
            return AgentRunResult(
                status="awaiting_approval",
                question=result["question"],
                final_response=None,
                sources=[],
                execution_log=result["execution_log"],
                interrupt_message=pending_interrupts[0].value,
            )
        return AgentRunResult(
            status="completed",
            question=result["question"],
            final_response=result["final_response"],
            sources=result["sources"],
            execution_log=result["execution_log"],
        )
