"""Integration test for the full ingest -> parse -> chunk -> embed -> store
pipeline. LOCAL TESTED against real PostgreSQL + pgvector; skips if
unreachable (see conftest.py).
"""

from tests.vectorstore.conftest import requires_postgres
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService


@requires_postgres
def test_index_repository_end_to_end(tmp_path, vector_store):
    (tmp_path / "main.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n"
    )
    (tmp_path / "README.md").write_text("# Demo\n")

    service = IndexingService(DeterministicLocalEmbeddingProvider(), vector_store)
    result = service.index_repository(str(tmp_path))

    assert result.chunks_indexed == 2  # add, sub — README is not chunked

    conn = vector_store.connect()
    try:
        query_embedding = DeterministicLocalEmbeddingProvider().embed(
            ["def add(a, b):\n    return a + b"]
        )[0]
        hits = vector_store.similarity_search(
            conn, query_embedding, top_k=1, repository_id=result.repository_id
        )
        assert hits[0].symbol_name == "add"
    finally:
        conn.close()


@requires_postgres
def test_index_empty_repository(tmp_path, vector_store):
    service = IndexingService(DeterministicLocalEmbeddingProvider(), vector_store)
    result = service.index_repository(str(tmp_path))

    assert result.chunks_indexed == 0
