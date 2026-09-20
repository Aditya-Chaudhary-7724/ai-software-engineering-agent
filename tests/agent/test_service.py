"""End-to-end integration tests for AgentService: real PostgreSQL +
pgvector, real Neo4j, deterministic local embeddings, and the stub LLM
provider. LOCAL TESTED; skips if either service is unreachable.
"""

import uuid

from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService

from graph.builder import GraphBuilder

from rag.llm.stub_provider import StubLLMProvider

from agent.service import AgentService
from tests.agent.conftest import neo4j_client, no_op_test_runner, requires_neo4j, requires_postgres, vector_store


def _index_and_build_graph(tmp_path, vector_store, neo4j_client):
    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    GraphBuilder(neo4j_client).build(str(tmp_path), ingestion_result, parsing_result)

    embedding_provider = DeterministicLocalEmbeddingProvider()
    index_result = IndexingService(embedding_provider, vector_store).index_repository(str(tmp_path))
    return index_result, embedding_provider


@requires_postgres
@requires_neo4j
def test_answer_task_produces_grounded_response(tmp_path, vector_store, neo4j_client, no_op_test_runner):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), no_op_test_runner)
    result = service.run(
        "What does the greet function do?",
        repository_id=index_result.repository_id,
        root_path=str(tmp_path.resolve()),
        thread_id=str(uuid.uuid4()),
    )

    assert result.status == "completed"
    assert "stub-llm" in result.final_response
    assert any("Classified task as 'answer'" in entry for entry in result.execution_log)
    assert any("Decision: route to 'answer'" in entry for entry in result.execution_log)


@requires_postgres
@requires_neo4j
def test_test_task_reports_no_test_command_when_repository_has_no_tests(
    tmp_path, vector_store, neo4j_client, no_op_test_runner
):
    """Phase 10 replaced the "not implemented" stub with a real sandbox test
    runner (see tests/sandbox/ for the runner's own tests). This repository
    genuinely has no test files, so the honest outcome is "no test command
    detected", not a fake pass or a "not implemented" placeholder.
    """
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), no_op_test_runner)
    result = service.run(
        "Run the tests",
        repository_id=index_result.repository_id,
        root_path=str(tmp_path.resolve()),
        thread_id=str(uuid.uuid4()),
    )

    assert result.status == "completed"
    assert "No supported test command was detected" in result.final_response


@requires_postgres
@requires_neo4j
def test_modify_task_pauses_for_human_approval(tmp_path, vector_store, neo4j_client, no_op_test_runner):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), no_op_test_runner)
    result = service.run(
        "Fix the greet function",
        repository_id=index_result.repository_id,
        root_path=str(tmp_path.resolve()),
        thread_id=str(uuid.uuid4()),
    )

    assert result.status == "awaiting_approval"
    assert result.final_response is None
    assert "human approval" in result.interrupt_message["message"]
    assert result.interrupt_message["relative_path"] == "main.py"
    assert "diff" in result.interrupt_message  # the actual diff is shown before approval, not after


@requires_postgres
@requires_neo4j
def test_modify_task_rejected_makes_no_changes(tmp_path, vector_store, neo4j_client, no_op_test_runner):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)
    thread_id = str(uuid.uuid4())

    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), no_op_test_runner)
    service.run(
        "Fix the greet function",
        repository_id=index_result.repository_id,
        root_path=str(tmp_path.resolve()),
        thread_id=thread_id,
    )
    result = service.resume(thread_id=thread_id, approved=False)

    assert result.status == "completed"
    assert "not approved" in result.final_response
    assert (tmp_path / "main.py").read_text() == "def greet():\n    return 'hi'\n"  # untouched


@requires_postgres
@requires_neo4j
def test_modify_task_approved_applies_the_change(tmp_path, vector_store, neo4j_client, no_op_test_runner):
    """Phase 9 wired in the real modification mechanism: an approved
    change is actually written to disk. The written content is the stub
    LLM's fixed placeholder text (not valid code — see
    modification/change_generator.py's docstring), which is expected:
    this proves the propose -> diff -> approve -> apply pipeline works
    end-to-end, not that generated content is meaningful (that needs a
    real LLM_API_KEY).

    Phase 10 then runs test verification after apply. This repository has
    no test files, so the honest outcome is "no supported test command was
    detected" — not a fake pass, and not the old "Tests: not run (Phase 10
    planned)" stub, since Phase 10 is now implemented.
    """
    original_content = "def greet():\n    return 'hi'\n"
    (tmp_path / "main.py").write_text(original_content)
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)
    thread_id = str(uuid.uuid4())

    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), no_op_test_runner)
    service.run(
        "Fix the greet function",
        repository_id=index_result.repository_id,
        root_path=str(tmp_path.resolve()),
        thread_id=thread_id,
    )
    result = service.resume(thread_id=thread_id, approved=True)

    assert result.status == "completed"
    assert "Applied approved change" in result.final_response
    assert "no supported test command was detected" in result.final_response
    new_content = (tmp_path / "main.py").read_text()
    assert new_content != original_content
    assert "stub-llm" in new_content
    assert any("approved" in entry.lower() for entry in result.execution_log)


@requires_postgres
@requires_neo4j
def test_modify_task_no_relevant_file_skips_approval(tmp_path, vector_store, neo4j_client, no_op_test_runner):
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), no_op_test_runner)
    result = service.run(
        "Fix the login bug",
        repository_id=index_result.repository_id,
        root_path=str(tmp_path.resolve()),
        thread_id=str(uuid.uuid4()),
    )

    assert result.status == "completed"  # no file found -> nothing to approve, reported immediately
    assert "No relevant file" in result.final_response


@requires_postgres
@requires_neo4j
def test_retries_are_bounded_on_empty_repository(tmp_path, vector_store, neo4j_client, no_op_test_runner):
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    service = AgentService(vector_store, neo4j_client, embedding_provider, StubLLMProvider(), no_op_test_runner)
    result = service.run(
        "What does this do?",
        repository_id=index_result.repository_id,
        root_path=str(tmp_path.resolve()),
        thread_id=str(uuid.uuid4()),
    )

    assert result.status == "completed"
    retry_entries = [e for e in result.execution_log if "retrying" in e]
    assert len(retry_entries) == 2  # MAX_RETRIES, then gives up rather than looping forever
    assert "No relevant code context was found" in result.final_response
