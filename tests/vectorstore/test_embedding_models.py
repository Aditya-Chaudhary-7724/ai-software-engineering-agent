"""Vectors from more than one embedding model (schema change 2026-11-12):
per-dimension columns, the exactly-one CHECK, the model recorded per
repository, and searches that refuse to mix models.

REGRESSION: before this change, retrieval embedded the query with whatever
provider was configured NOW and silently ranked vectors that a DIFFERENT
model had produced (same 1536 dimension, e.g. hash stand-in vs OpenAI), so
results were meaningless without any error. The first test below returns
hits on the old code and raises ReindexRequiredError on the new code.

Real PostgreSQL (ai_swe_agent_test, plus throwaway databases for the
upgrade test); never the development or application database.
"""

import hashlib

import psycopg
import pytest

from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.exceptions import EmbeddingDimensionError, ReindexRequiredError
from vectorstore.migrations import apply_schema_to_database
from vectorstore.models import CodeChunk
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore

from tests.vectorstore.conftest import TEST_DATABASE_URL, requires_postgres, vector_store  # noqa: F401
from tests.vectorstore.test_migrations import fresh_database_url  # noqa: F401

pytestmark = requires_postgres


class _HashProvider(EmbeddingProvider):
    """A deterministic test provider of any dimension and model id."""

    def __init__(self, dimension: int, model_id: str) -> None:
        self.dimension = dimension
        self.model_id = model_id

    def embed(self, texts):
        out = []
        for text in texts:
            digest = hashlib.sha256((self.model_id + text).encode()).digest()
            out.append([(digest[i % len(digest)] / 127.5) - 1.0 for i in range(self.dimension)])
        return out


HASH_1536 = DeterministicLocalEmbeddingProvider()
OTHER_1536 = _HashProvider(1536, "openai")  # same dimension, DIFFERENT model
LOCAL_384 = _HashProvider(384, "fastembed:test-384")


def _repo(tmp_path, name="repo"):
    root = tmp_path / name
    root.mkdir()
    (root / "calc.py").write_text("def add_numbers(a, b):\n    return a + b\n\n\ndef sub_numbers(a, b):\n    return a - b\n")
    return root


def _search(store, provider, repository_id, text="def add_numbers(a, b):\n    return a + b"):
    conn = store.connect()
    try:
        query = provider.embed([text])[0]
        return store.similarity_search(conn, query, top_k=5, repository_id=repository_id, embedding_model=provider.model_id)
    finally:
        conn.close()


def _row(sql, params=()):
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        return conn.execute(sql, params).fetchone()


# -- the regression -------------------------------------------------------------


def test_a_query_from_a_different_model_of_the_same_dimension_is_refused(tmp_path, vector_store):  # noqa: F811
    repository_id = IndexingService(HASH_1536, vector_store).index_repository(str(_repo(tmp_path))).repository_id

    assert _search(vector_store, HASH_1536, repository_id)  # same model: fine
    with pytest.raises(ReindexRequiredError) as caught:
        _search(vector_store, OTHER_1536, repository_id)
    assert (caught.value.indexed_with, caught.value.active) == ("deterministic_local", "openai")


def test_a_repository_indexed_before_models_were_recorded_requires_a_reindex(tmp_path, vector_store):  # noqa: F811
    repository_id = IndexingService(HASH_1536, vector_store).index_repository(str(_repo(tmp_path))).repository_id
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        conn.execute("UPDATE repositories SET embedding_model = NULL WHERE id = %s", (repository_id,))

    with pytest.raises(ReindexRequiredError) as caught:
        _search(vector_store, HASH_1536, repository_id)
    assert caught.value.indexed_with is None


def test_a_query_of_another_dimension_is_refused_as_reindex_not_a_database_error(tmp_path, vector_store):  # noqa: F811
    repository_id = IndexingService(HASH_1536, vector_store).index_repository(str(_repo(tmp_path))).repository_id
    with pytest.raises(ReindexRequiredError):
        _search(vector_store, LOCAL_384, repository_id)


# -- 384-dimension storage ----------------------------------------------------------


def test_384_dimension_vectors_go_to_their_own_column_and_are_searchable(tmp_path, vector_store):  # noqa: F811
    repository_id = IndexingService(LOCAL_384, vector_store).index_repository(str(_repo(tmp_path))).repository_id

    assert _row("SELECT embedding_model FROM repositories WHERE id = %s", (repository_id,)) == ("fastembed:test-384",)
    counts = _row(
        "SELECT count(*) FILTER (WHERE embedding_384 IS NOT NULL), count(*) FILTER (WHERE embedding IS NOT NULL) "
        "FROM code_chunks WHERE repository_id = %s",
        (repository_id,),
    )
    assert counts[0] > 0 and counts[1] == 0
    hits = _search(vector_store, LOCAL_384, repository_id)
    assert hits[0].symbol_name == "add_numbers" and hits[0].distance == pytest.approx(0.0, abs=1e-6)


def test_reindexing_with_another_model_switches_column_and_recorded_model(tmp_path, vector_store):  # noqa: F811
    root = _repo(tmp_path)
    first = IndexingService(HASH_1536, vector_store).index_repository(str(root)).repository_id
    second = IndexingService(LOCAL_384, vector_store).index_repository(str(root)).repository_id

    assert first == second
    assert _row("SELECT count(*) FROM code_chunks WHERE repository_id = %s AND embedding IS NOT NULL", (first,)) == (0,)
    assert _search(vector_store, LOCAL_384, first)
    with pytest.raises(ReindexRequiredError):
        _search(vector_store, HASH_1536, first)


def test_cross_repository_search_only_returns_repositories_of_the_query_model(tmp_path, vector_store):  # noqa: F811
    a = IndexingService(HASH_1536, vector_store).index_repository(str(_repo(tmp_path, "a"))).repository_id
    IndexingService(LOCAL_384, vector_store).index_repository(str(_repo(tmp_path, "b")))
    IndexingService(OTHER_1536, vector_store).index_repository(str(_repo(tmp_path, "c")))

    conn = vector_store.connect()
    try:
        hits = vector_store.similarity_search(
            conn, HASH_1536.embed(["add_numbers"])[0], top_k=50, embedding_model=HASH_1536.model_id
        )
    finally:
        conn.close()
    repository_ids = {_row("SELECT repository_id FROM code_chunks WHERE id = %s", (h.chunk_id,))[0] for h in hits}
    assert repository_ids == {a}


# -- refusals before anything is written ---------------------------------------------


def test_an_unsupported_dimension_is_refused_before_embedding_or_writing(tmp_path, vector_store):  # noqa: F811
    class Counting768(_HashProvider):
        calls = 0

        def embed(self, texts):
            Counting768.calls += 1
            return super().embed(texts)

    with pytest.raises(EmbeddingDimensionError, match="unsupported embedding dimension 768"):
        IndexingService(Counting768(768, "x:768"), vector_store).index_repository(str(_repo(tmp_path)))
    assert Counting768.calls == 0
    assert _row("SELECT count(*) FROM repositories") == (0,)


def test_a_batch_of_mixed_dimensions_is_refused(vector_store):  # noqa: F811
    chunk = CodeChunk("a.py", "Python", "function", "f", "f", 1, 1, "def f(): pass")
    conn = vector_store.connect()
    try:
        repository_id = vector_store.get_or_create_repository(conn, "r", "/tmp/mixed", embedding_model="x")
        with pytest.raises(EmbeddingDimensionError, match="mixed"):
            vector_store.insert_chunks(conn, repository_id, [chunk, chunk], [[0.0] * 1536, [0.0] * 384])
    finally:
        conn.close()


@pytest.mark.parametrize("columns, values", [("", ""), (", embedding, embedding_384", ", %(v1536)s::vector, %(v384)s::vector")])
def test_the_database_enforces_exactly_one_embedding_per_chunk(vector_store, columns, values):  # noqa: F811
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        repository_id = conn.execute(
            "INSERT INTO repositories (name, root_path, embedding_model) VALUES ('r', '/tmp/check', 'x') RETURNING id"
        ).fetchone()[0]
        conn.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO code_chunks (repository_id, relative_path, chunk_type, start_line, end_line, content"
                f"{columns}) VALUES (%(r)s, 'a.py', 'function', 1, 1, 'x'{values})",
                {"r": repository_id, "v1536": str([0.0] * 1536), "v384": str([0.0] * 384)},
            )


# -- upgrading an existing (pre-change) database ------------------------------------------

_PRE_CHANGE_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE repositories (
    id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, root_path TEXT NOT NULL UNIQUE,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE code_chunks (
    id BIGSERIAL PRIMARY KEY,
    repository_id BIGINT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL, language TEXT, chunk_type TEXT NOT NULL, symbol_name TEXT,
    qualified_name TEXT, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, content TEXT NOT NULL,
    embedding VECTOR(1536) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def test_upgrading_a_pre_change_database_keeps_its_data_and_marks_it_for_reindex(fresh_database_url):  # noqa: F811
    with psycopg.connect(fresh_database_url) as conn:
        conn.execute(_PRE_CHANGE_SCHEMA)
        repository_id = conn.execute("INSERT INTO repositories (name, root_path) VALUES ('old', '/tmp/old') RETURNING id").fetchone()[0]
        conn.execute(
            "INSERT INTO code_chunks (repository_id, relative_path, chunk_type, start_line, end_line, content, embedding) "
            "VALUES (%s, 'a.py', 'function', 1, 2, 'def old(): pass', %s::vector)",
            (repository_id, str([0.5] * 1536)),
        )

    apply_schema_to_database(fresh_database_url)
    apply_schema_to_database(fresh_database_url)  # idempotent

    with psycopg.connect(fresh_database_url) as conn:
        assert conn.execute("SELECT count(*), count(embedding), count(embedding_384) FROM code_chunks").fetchone() == (1, 1, 0)
        assert conn.execute("SELECT embedding_model FROM repositories").fetchone() == (None,)
        assert conn.execute(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'code_chunks_exactly_one_embedding'"
        ).fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_code_chunks_embedding_384_hnsw'").fetchone() == (1,)

    store = VectorStore(fresh_database_url)
    with pytest.raises(ReindexRequiredError):
        _search(store, HASH_1536, repository_id)
