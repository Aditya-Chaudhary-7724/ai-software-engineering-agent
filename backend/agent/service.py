"""Public entry point for the agent, hiding LangGraph's interrupt/resume
mechanics behind a plain run()/resume() pair.

Each conversation needs a stable `thread_id` (any caller-chosen string)
so the checkpointer can resume the exact paused state after a human
approval decision — the same `thread_id` must be passed to both `run`
and the matching `resume`.
"""

from typing import Optional

from langgraph.types import Command

from graph.client import Neo4jClient
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.store import VectorStore

from rag.llm.base import LLMProvider

from sandbox.base import TestRunner

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
    ) -> None:
        self._graph = build_agent_graph(vector_store, neo4j_client, embedding_provider, llm_provider, test_runner)

    def run(self, question: str, repository_id: int, root_path: str, thread_id: str) -> AgentRunResult:
        config = {"configurable": {"thread_id": thread_id}}
        result = self._graph.invoke(initial_state(question, repository_id, root_path), config=config)
        return self._to_result(result)

    def resume(self, thread_id: str, approved: bool) -> AgentRunResult:
        config = {"configurable": {"thread_id": thread_id}}
        result = self._graph.invoke(Command(resume=approved), config=config)
        return self._to_result(result)

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
