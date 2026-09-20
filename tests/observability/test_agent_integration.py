"""Integration tests: tracing wired into the REAL `AgentService` /
LangGraph workflow — real PostgreSQL + Neo4j, `StubLLMProvider`, a real
`DockerTestRunner` (never invoked, since these sample repos have no
test files). Proves Phase 13 is integrated into existing execution,
not a disconnected demo, and that a broken recorder never breaks a
real agent run — the single most important fail-safety property this
phase adds.
"""

import uuid

from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService

from graph.builder import GraphBuilder

from rag.llm.stub_provider import StubLLMProvider

from sandbox.docker_runner import DockerTestRunner

from observability.recorder import InMemoryRecorder, Recorder
from observability.tracer import Tracer

from agent.service import AgentService

from tests.agent.conftest import neo4j_client, requires_neo4j, requires_postgres, vector_store


def _index_and_build_graph(tmp_path, vector_store, neo4j_client):
    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    GraphBuilder(neo4j_client).build(str(tmp_path), ingestion_result, parsing_result)

    embedding_provider = DeterministicLocalEmbeddingProvider()
    index_result = IndexingService(embedding_provider, vector_store).index_repository(str(tmp_path))
    return index_result, embedding_provider


@requires_postgres
@requires_neo4j
def test_an_answer_run_produces_a_complete_correlated_trace(tmp_path, vector_store, neo4j_client):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), DockerTestRunner(), tracer)
    thread_id = str(uuid.uuid4())

    result = service.run("Where is greet implemented?", index_result.repository_id, str(tmp_path.resolve()), thread_id)

    assert result.status == "completed"
    trace = recorder.get_trace(thread_id)
    assert trace is not None
    assert trace.trace_id == thread_id  # trace_id IS the thread_id — no second ID invented
    assert trace.status == "completed"
    assert trace.ended_at is not None

    span_names = {s.name for s in trace.spans}
    assert {"agent_run", "task_analyzer", "planner", "repository_search", "graph_search", "code_analyzer", "decision", "answer"} <= span_names

    # Every top-level agent node is correlated directly under the one
    # agent_run span (the llm_call span checked below is nested one
    # level deeper still, under "answer" — not directly under agent_run).
    agent_run_span = next(s for s in trace.spans if s.name == "agent_run")
    top_level_node_names = {"task_analyzer", "planner", "repository_search", "graph_search", "code_analyzer", "decision", "answer"}
    top_level_spans = [s for s in trace.spans if s.name in top_level_node_names]
    assert len(top_level_spans) == len(top_level_node_names)
    assert all(s.parent_span_id == agent_run_span.span_id for s in top_level_spans)

    # An LLM call happened inside the answer node and is nested under it.
    answer_span = next(s for s in trace.spans if s.name == "answer")
    llm_span = next(s for s in trace.spans if s.kind == "llm")
    assert llm_span.parent_span_id == answer_span.span_id
    assert llm_span.attributes["llm_provider"] == "StubLLMProvider"


@requires_postgres
@requires_neo4j
def test_a_modify_run_pauses_the_trace_at_human_approval(tmp_path, vector_store, neo4j_client):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), DockerTestRunner(), tracer)
    thread_id = str(uuid.uuid4())

    result = service.run("Fix the greet function", index_result.repository_id, str(tmp_path.resolve()), thread_id)
    assert result.status == "awaiting_approval"

    trace = recorder.get_trace(thread_id)
    assert trace.status == "awaiting_approval"
    assert trace.ended_at is None  # not yet a terminal state
    approval_span = next(s for s in trace.spans if s.name == "human_approval")
    assert approval_span.status == "interrupted"

    # Resuming continues the SAME trace, not a new one.
    resumed = service.resume(thread_id, approved=True)
    assert resumed.status == "completed"
    trace_after_resume = recorder.get_trace(thread_id)
    assert trace_after_resume.status == "completed"
    assert trace_after_resume.ended_at is not None
    assert any(s.name == "agent_resume" for s in trace_after_resume.spans)
    assert any(s.name == "apply_change" for s in trace_after_resume.spans)


class _BrokenRecorder(Recorder):
    def record_span(self, span):
        raise RuntimeError("disk is full")

    def record_trace(self, trace):
        raise RuntimeError("disk is full")


@requires_postgres
@requires_neo4j
def test_a_broken_recorder_does_not_break_a_real_agent_run(tmp_path, vector_store, neo4j_client):
    """The single most important fail-safety property this phase adds:
    even with EVERY recorder call failing, a real end-to-end agent run
    (through real retrieval, real graph search, a real LLM-provider
    call, and a real routing decision) must complete exactly as if no
    tracer were involved at all.
    """
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    tracer = Tracer(_BrokenRecorder())
    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), DockerTestRunner(), tracer)

    result = service.run(
        "Where is greet implemented?", index_result.repository_id, str(tmp_path.resolve()), str(uuid.uuid4())
    )

    assert result.status == "completed"
    assert "stub-llm" in result.final_response


@requires_postgres
@requires_neo4j
def test_agent_behaves_identically_with_and_without_a_tracer(tmp_path, vector_store, neo4j_client):
    """Backward compatibility, proven, not just asserted: the same
    question against the same repository produces the same outcome
    whether or not a tracer is supplied.
    """
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    service_without_tracer = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), DockerTestRunner())
    service_with_tracer = AgentService(
        vector_store, neo4j_client, embedding_provider, StubLLMProvider(), DockerTestRunner(), Tracer(InMemoryRecorder())
    )

    result_a = service_without_tracer.run(
        "Where is greet implemented?", index_result.repository_id, str(tmp_path.resolve()), str(uuid.uuid4())
    )
    result_b = service_with_tracer.run(
        "Where is greet implemented?", index_result.repository_id, str(tmp_path.resolve()), str(uuid.uuid4())
    )

    assert result_a.status == result_b.status
    assert result_a.final_response == result_b.final_response
    assert result_a.execution_log == result_b.execution_log
