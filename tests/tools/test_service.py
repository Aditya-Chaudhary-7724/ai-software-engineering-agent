"""End-to-end integration tests for the default tool registry: real
PostgreSQL + pgvector, real Neo4j, deterministic local embeddings.
LOCAL TESTED; skips if either service is unreachable.
"""

from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService

from graph.builder import GraphBuilder

from tools.service import build_default_registry
from tests.tools.conftest import neo4j_client, requires_neo4j, requires_postgres, vector_store


def _index_and_build_graph(tmp_path, vector_store, neo4j_client):
    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    GraphBuilder(neo4j_client).build(str(tmp_path), ingestion_result, parsing_result)

    embedding_provider = DeterministicLocalEmbeddingProvider()
    index_result = IndexingService(embedding_provider, vector_store).index_repository(str(tmp_path))
    return index_result, embedding_provider


@requires_postgres
@requires_neo4j
def test_search_code_tool_finds_indexed_chunk(tmp_path, vector_store, neo4j_client):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    registry = build_default_registry(vector_store, neo4j_client, embedding_provider)
    result = registry.invoke("search_code", {"repository_id": index_result.repository_id, "query": "greet"})

    assert result.success is True
    assert any(h["symbol_name"] == "greet" for h in result.data["hits"])


@requires_postgres
@requires_neo4j
def test_search_symbol_tool_finds_class(tmp_path, vector_store, neo4j_client):
    (tmp_path / "models.py").write_text("class Animal:\n    pass\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    registry = build_default_registry(vector_store, neo4j_client, embedding_provider)
    result = registry.invoke("search_symbol", {"root_path": str(tmp_path.resolve()), "name": "Animal"})

    assert result.success is True
    assert result.data["matches"][0]["kind"] == "Class"


@requires_postgres
@requires_neo4j
def test_graph_query_repository_summary(tmp_path, vector_store, neo4j_client):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    registry = build_default_registry(vector_store, neo4j_client, embedding_provider)
    result = registry.invoke(
        "graph_query", {"root_path": str(tmp_path.resolve()), "query_type": "repository_summary"}
    )

    assert result.success is True
    assert result.data["results"][0]["files"] == 1


@requires_postgres
@requires_neo4j
def test_graph_query_invalid_type_returns_error(tmp_path, vector_store, neo4j_client):
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    registry = build_default_registry(vector_store, neo4j_client, embedding_provider)
    result = registry.invoke(
        "graph_query", {"root_path": str(tmp_path.resolve()), "query_type": "drop_everything"}
    )

    assert result.success is False
    assert "Unknown query_type" in result.error


@requires_postgres
@requires_neo4j
def test_get_dependencies_resolves_import(tmp_path, vector_store, neo4j_client):
    (tmp_path / "models.py").write_text("class Animal:\n    pass\n")
    (tmp_path / "main.py").write_text("from models import Animal\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    registry = build_default_registry(vector_store, neo4j_client, embedding_provider)
    result = registry.invoke(
        "get_dependencies", {"root_path": str(tmp_path.resolve()), "relative_path": "main.py"}
    )

    assert result.success is True
    assert result.data["dependencies"][0]["resolved_file"] == "models.py"


@requires_postgres
@requires_neo4j
def test_get_callers_reports_not_available(tmp_path, vector_store, neo4j_client):
    (tmp_path / "main.py").write_text("def greet():\n    return 'hi'\n")
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)

    registry = build_default_registry(vector_store, neo4j_client, embedding_provider)
    result = registry.invoke(
        "get_callers", {"root_path": str(tmp_path.resolve()), "relative_path": "main.py", "symbol_name": "greet"}
    )

    assert result.success is True  # the call itself succeeds; the capability honestly doesn't
    assert result.data["available"] is False
    assert "CALLS relationship" in result.data["reason"]


@requires_postgres
@requires_neo4j
def test_all_tools_are_registered(tmp_path, vector_store, neo4j_client):
    index_result, embedding_provider = _index_and_build_graph(tmp_path, vector_store, neo4j_client)
    registry = build_default_registry(vector_store, neo4j_client, embedding_provider)

    names = {t.name for t in registry.list_tools()}
    assert names == {
        "list_files", "read_file", "analyze_code", "search_code",
        "search_symbol", "graph_query", "get_dependencies", "get_callers", "get_callees",
    }
