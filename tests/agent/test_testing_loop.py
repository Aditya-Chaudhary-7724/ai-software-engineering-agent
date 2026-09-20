"""Phase 10: tests the agent's testing-loop wiring — run tests -> inspect
failure -> decide whether to fix -> propose change -> human approval ->
apply -> rerun tests — using FakeTestRunner (see conftest.py) so the
routing/bounding logic in agent/nodes.py's run_tests_after_apply_node is
exercised deterministically, without Docker. Real sandboxed execution is
covered by tests/sandbox/ and the end-to-end manual demo.
"""

import uuid

import agent.nodes as nodes_module
from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService

from graph.builder import GraphBuilder

from rag.llm.stub_provider import StubLLMProvider

from agent.service import AgentService
from tests.agent.conftest import FakeTestRunner, make_test_result, neo4j_client, requires_neo4j, requires_postgres, vector_store


def _index_and_build_graph(tmp_path, vector_store, neo4j_client):
    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    GraphBuilder(neo4j_client).build(str(tmp_path), ingestion_result, parsing_result)

    embedding_provider = DeterministicLocalEmbeddingProvider()
    index_result = IndexingService(embedding_provider, vector_store).index_repository(str(tmp_path))
    return index_result, embedding_provider


@requires_postgres
@requires_neo4j
def test_tests_passing_after_apply_ends_the_loop_with_one_approval(tmp_path, vector_store, neo4j_client):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    test_runner = FakeTestRunner([make_test_result(passed=True)])
    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), test_runner)
    thread_id = str(uuid.uuid4())

    service.run(
        "Fix the greet function",
        repository_id=index_result.repository_id,
        root_path=str(tmp_path.resolve()),
        thread_id=thread_id,
    )
    result = service.resume(thread_id=thread_id, approved=True)

    assert result.status == "completed"
    assert "Tests: passed." in result.final_response
    assert test_runner.call_count == 1  # no fix attempt was needed


@requires_postgres
@requires_neo4j
def test_failing_tests_trigger_a_bounded_number_of_approved_fix_attempts(tmp_path, vector_store, neo4j_client):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    # 1 initial run + MAX_FIX_ITERATIONS(2) retries = 3 calls, all failing.
    test_runner = FakeTestRunner(
        [
            make_test_result(passed=False, stderr="AssertionError: first failure"),
            make_test_result(passed=False, stderr="AssertionError: second failure"),
            make_test_result(passed=False, stderr="AssertionError: third failure"),
        ]
    )
    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), test_runner)
    thread_id = str(uuid.uuid4())

    result = service.run(
        "Fix the greet function",
        repository_id=index_result.repository_id,
        root_path=str(tmp_path.resolve()),
        thread_id=thread_id,
    )
    approvals = 0
    while result.status == "awaiting_approval":
        approvals += 1
        assert approvals <= 10, "the fix loop must terminate — it did not"
        result = service.resume(thread_id=thread_id, approved=True)

    assert result.status == "completed"
    assert approvals == 3  # initial approval + 2 fix-attempt approvals, never more
    assert test_runner.call_count == 3
    assert "Giving up after 2 fix attempt(s)" in result.final_response
    assert "fix-attempt limit reached" in result.final_response
    fix_attempt_logs = [e for e in result.execution_log if "attempting fix" in e]
    assert len(fix_attempt_logs) == 2


@requires_postgres
@requires_neo4j
def test_a_fix_attempt_still_requires_its_own_human_approval(tmp_path, vector_store, neo4j_client):
    """The human-approval gate must not be bypassed on retries: rejecting
    the SECOND approval (the first fix attempt) must stop the loop
    immediately, with no second test run and no further changes.
    """
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    test_runner = FakeTestRunner([make_test_result(passed=False, stderr="boom")])
    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), test_runner)
    thread_id = str(uuid.uuid4())

    result = service.run(
        "Fix the greet function",
        repository_id=index_result.repository_id,
        root_path=str(tmp_path.resolve()),
        thread_id=thread_id,
    )
    assert result.status == "awaiting_approval"
    result = service.resume(thread_id=thread_id, approved=True)  # approve the original change
    assert result.status == "awaiting_approval"  # now paused on the fix attempt

    result = service.resume(thread_id=thread_id, approved=False)  # reject the fix

    assert result.status == "completed"
    assert "not approved" in result.final_response
    assert test_runner.call_count == 1  # the fix's tests were never run


@requires_postgres
@requires_neo4j
def test_time_budget_exhausted_stops_the_loop_even_within_iteration_limit(tmp_path, vector_store, neo4j_client, monkeypatch):
    """Forces MAX_LOOP_SECONDS to 0 so the very first post-apply test
    failure already exceeds the wall-clock budget, proving the time bound
    is enforced independently of the iteration-count bound (fix_iteration
    is still 0, well under MAX_FIX_ITERATIONS, when the loop stops).
    """
    monkeypatch.setattr(nodes_module, "MAX_LOOP_SECONDS", 0)

    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    test_runner = FakeTestRunner([make_test_result(passed=False, stderr="boom")])
    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), test_runner)
    thread_id = str(uuid.uuid4())

    service.run(
        "Fix the greet function",
        repository_id=index_result.repository_id,
        root_path=str(tmp_path.resolve()),
        thread_id=thread_id,
    )
    result = service.resume(thread_id=thread_id, approved=True)

    assert result.status == "completed"
    assert test_runner.call_count == 1
    assert "time budget exhausted" in result.final_response
