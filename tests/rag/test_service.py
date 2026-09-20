"""Integration test for the full RAGService pipeline: real Postgres +
pgvector retrieval and keyword search, deterministic local embeddings,
and the stub LLM provider (no LLM_API_KEY needed). LOCAL TESTED; skips
if Postgres isn't reachable.
"""

from tests.rag.conftest import requires_postgres
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService

from rag.llm.stub_provider import StubLLMProvider
from rag.service import RAGService


@requires_postgres
def test_rag_service_answers_with_sources(tmp_path, vector_store):
    (tmp_path / "auth.py").write_text(
        "def authenticate_user(username, password):\n"
        "    return check(username, password)\n\n"
        "def add(a, b):\n"
        "    return a + b\n"
    )

    embedding_provider = DeterministicLocalEmbeddingProvider()
    indexing_service = IndexingService(embedding_provider, vector_store)
    index_result = indexing_service.index_repository(str(tmp_path))

    rag_service = RAGService(vector_store, embedding_provider, StubLLMProvider())
    answer = rag_service.answer(
        "Where is authentication implemented?", repository_id=index_result.repository_id
    )

    assert answer.context_chunk_count > 0
    assert len(answer.sources) == answer.context_chunk_count
    assert any(s.symbol_name == "authenticate_user" for s in answer.sources)
    assert "stub-llm" in answer.answer


@requires_postgres
def test_rag_service_no_matching_repository_returns_no_context(vector_store):
    embedding_provider = DeterministicLocalEmbeddingProvider()
    rag_service = RAGService(vector_store, embedding_provider, StubLLMProvider())

    answer = rag_service.answer("What does this do?", repository_id=999999)

    assert answer.context_chunk_count == 0
    assert answer.sources == []
    assert "No relevant code context was found" in answer.answer
