"""The REAL bge-small model through fastembed (no fake). Skipped where
fastembed is not installed (it requires Python >= 3.10; CI and the image
run 3.11). Downloads the pinned revision (~67 MB) once, unless
EMBEDDING_MODEL_PATH points at a pre-downloaded copy."""

import math
import os

import pytest

import config

fastembed = pytest.importorskip("fastembed", reason="fastembed not installed (requires Python >= 3.10)")

from vectorstore.embeddings import fastembed_provider as module  # noqa: E402
from vectorstore.embeddings.fastembed_provider import FastEmbedEmbeddingProvider  # noqa: E402

MODEL = config.FASTEMBED_MODELS[config.DEFAULT_FASTEMBED_MODEL]


@pytest.fixture(scope="module")
def provider():
    return FastEmbedEmbeddingProvider(
        model_name=MODEL.name, dimension=MODEL.dimension, hf_repo=MODEL.hf_repo, hf_revision=MODEL.hf_revision,
        batch_size=8, model_path=os.environ.get("REAL_EMBEDDING_MODEL_PATH") or None, threads=2,
    )


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_the_pinned_revision_is_what_gets_downloaded():
    path = module.download_pinned_model(MODEL.hf_repo, MODEL.hf_revision)
    assert MODEL.hf_revision in path
    assert os.path.isfile(os.path.join(path, "model_optimized.onnx"))


def test_real_vectors_have_the_declared_dimension_and_are_deterministic(provider):
    first = provider.embed(["def add_numbers(a, b):\n    return a + b"])
    again = provider.embed(["def add_numbers(a, b):\n    return a + b"])
    assert len(first[0]) == MODEL.dimension == 384
    assert _cosine(first[0], again[0]) > 0.9999


def test_embeddings_are_semantic_unlike_the_hash_stand_in(provider):
    query, related, unrelated = provider.embed(
        [
            "function that adds two numbers",
            "def add_numbers(a, b):\n    return a + b",
            "class DatabaseConnectionPool:\n    def acquire(self):\n        return self._pool.get()",
        ]
    )
    assert _cosine(query, related) > _cosine(query, unrelated) + 0.05


def test_real_model_end_to_end_index_and_semantic_search_in_postgres(provider, tmp_path):
    """Real bge-small vectors stored in `embedding_384` and searched in
    PostgreSQL: a natural-language query finds the right function."""
    from tests.vectorstore.conftest import TEST_DATABASE_URL, _postgres_available
    from vectorstore.migrations import apply_schema
    from vectorstore.service import IndexingService
    from vectorstore.store import VectorStore

    if not _postgres_available():
        pytest.skip("PostgreSQL test database not reachable")
    (tmp_path / "calc.py").write_text("def add_numbers(a, b):\n    return a + b\n")
    (tmp_path / "pool.py").write_text("class ConnectionPool:\n    def acquire(self):\n        return self._idle.pop()\n")
    store = VectorStore(TEST_DATABASE_URL)
    conn = store.connect()
    apply_schema(conn)
    conn.close()
    try:
        repository_id = IndexingService(provider, store).index_repository(str(tmp_path)).repository_id
        conn = store.connect()
        try:
            hits = store.similarity_search(
                conn, provider.embed(["sum two values"])[0], top_k=2, repository_id=repository_id,
                embedding_model=provider.model_id,
            )
            stored = conn.execute(
                "SELECT count(embedding_384), count(embedding) FROM code_chunks WHERE repository_id = %s", (repository_id,)
            ).fetchone()
        finally:
            conn.close()
        assert stored[0] >= 2 and stored[1] == 0
        assert hits[0].relative_path == "calc.py"
    finally:
        conn = store.connect()
        conn.execute("TRUNCATE code_chunks, repositories RESTART IDENTITY CASCADE")
        conn.commit()
        conn.close()
