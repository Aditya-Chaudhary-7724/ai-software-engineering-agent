"""Integration tests for the agent evaluation runner — real PostgreSQL
+ Neo4j, real AgentService/LangGraph workflow, StubLLMProvider.

Also tests the RUNNER'S OWN detection logic directly (not just that the
real agent happens to behave correctly): `_evaluate_routing_case` is
fed a deliberately wrong expectation and must report `passed=False` —
proving this evaluation code can actually detect a failure, not only
ever report success.
"""

from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider

from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.service import IndexingService

from graph.builder import GraphBuilder

from rag.llm.stub_provider import StubLLMProvider

from sandbox.docker_runner import DockerTestRunner

from agent.service import AgentService

from evaluation.dataset import AGENT_CASES, AgentCase
from evaluation.runners.agent import _evaluate_routing_case, run_agent_evaluation

from tests.evaluation.conftest import neo4j_client, requires_neo4j, requires_postgres, vector_store


@requires_postgres
@requires_neo4j
def test_returns_one_result_per_case_plus_the_bounded_retry_case(vector_store, neo4j_client):
    results = run_agent_evaluation(vector_store, neo4j_client)

    expected_ids = {c.case_id for c in AGENT_CASES} | {"agent-bounded-retry"}
    assert {r.case_id for r in results} == expected_ids
    assert all(r.category == "agent" for r in results)


@requires_postgres
@requires_neo4j
def test_all_cases_pass_against_the_real_agent(vector_store, neo4j_client):
    results = run_agent_evaluation(vector_store, neo4j_client)

    for result in results:
        assert result.passed, f"{result.case_id} failed: {[m for m in result.metrics if m.passed is False]}"


@requires_postgres
@requires_neo4j
def test_runner_correctly_detects_a_wrong_expectation(tmp_path, vector_store, neo4j_client):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    root_path = str(tmp_path.resolve())

    ingestion_result = IngestionService().ingest(root_path)
    parsing_result = ParsingService().parse_repository(root_path, ingestion_result)
    embedding_provider = DeterministicLocalEmbeddingProvider()
    index_result = IndexingService(embedding_provider, vector_store).index_repository(root_path)
    GraphBuilder(neo4j_client).build(root_path, ingestion_result, parsing_result)

    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), DockerTestRunner())

    wrong_case = AgentCase(
        case_id="deliberately-wrong",
        description="An answer question, deliberately asserted to be a 'modify' routing.",
        question="Where is the login route implemented?",
        expected_task_type="modify",  # wrong on purpose
        expected_decision="modify",  # wrong on purpose
    )

    try:
        result = _evaluate_routing_case(wrong_case, service, index_result.repository_id, root_path)
    finally:
        conn = vector_store.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM code_chunks WHERE repository_id = %s", (index_result.repository_id,))
                cur.execute("DELETE FROM repositories WHERE id = %s", (index_result.repository_id,))
            conn.commit()
        finally:
            conn.close()
        neo4j_client.run("MATCH (n {repository_root_path: $root_path}) DETACH DELETE n", root_path=root_path)
        neo4j_client.run("MATCH (r:Repository {root_path: $root_path}) DETACH DELETE r", root_path=root_path)

    assert result.passed is False
    classification_metric = next(m for m in result.metrics if m.name == "task_classification_correct")
    assert classification_metric.passed is False
