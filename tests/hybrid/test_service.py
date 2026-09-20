"""End-to-end integration test for HybridRAGService: real PostgreSQL +
pgvector, real Neo4j, deterministic local embeddings, and the stub LLM
provider. LOCAL TESTED; skips if either service is unreachable.
"""

from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService

from graph.builder import GraphBuilder

from rag.llm.stub_provider import StubLLMProvider

from hybrid.service import HybridRAGService
from tests.hybrid.conftest import neo4j_client, requires_neo4j, requires_postgres, vector_store


@requires_postgres
@requires_neo4j
def test_hybrid_service_pulls_in_dependency_via_graph(tmp_path, vector_store, neo4j_client):
    (tmp_path / "auth_service.py").write_text(
        "def login_user():\n    return generate_jwt()\n\ndef generate_jwt():\n    return 'token'\n"
    )
    (tmp_path / "routes.py").write_text(
        "from auth_service import login_user\n\ndef login_route():\n    return login_user()\n"
    )

    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    GraphBuilder(neo4j_client).build(str(tmp_path), ingestion_result, parsing_result)

    embedding_provider = DeterministicLocalEmbeddingProvider()
    index_result = IndexingService(embedding_provider, vector_store).index_repository(str(tmp_path))

    service = HybridRAGService(vector_store, neo4j_client, embedding_provider, StubLLMProvider())
    answer = service.answer(
        "Where is the login route?",
        repository_id=index_result.repository_id,
        root_path=str(tmp_path.resolve()),
    )

    source_symbols = {s.symbol_name for s in answer.sources}
    assert "login_route" in source_symbols
    assert "generate_jwt" in source_symbols

    # This repo is tiny enough that vector_top_k also happens to return
    # every chunk regardless of relevance, so this doesn't prove graph
    # evidence was the *only* way generate_jwt could surface (that
    # rigorous claim is tested directly in test_graph_expansion.py with
    # controlled seeds). It does prove the graph expansion step ran and
    # tagged its contribution correctly end-to-end.
    generate_jwt_source = next(s for s in answer.sources if s.symbol_name == "generate_jwt")
    assert any("graph:" in tag for tag in generate_jwt_source.found_via)


@requires_postgres
@requires_neo4j
def test_hybrid_service_empty_repository_returns_no_context(tmp_path, vector_store, neo4j_client):
    ingestion_result = IngestionService().ingest(str(tmp_path))
    parsing_result = ParsingService().parse_repository(str(tmp_path), ingestion_result)
    GraphBuilder(neo4j_client).build(str(tmp_path), ingestion_result, parsing_result)

    embedding_provider = DeterministicLocalEmbeddingProvider()
    index_result = IndexingService(embedding_provider, vector_store).index_repository(str(tmp_path))

    service = HybridRAGService(vector_store, neo4j_client, embedding_provider, StubLLMProvider())
    answer = service.answer(
        "What does this do?", repository_id=index_result.repository_id, root_path=str(tmp_path.resolve())
    )

    assert answer.context_chunk_count == 0
    assert "No relevant code context was found" in answer.answer
