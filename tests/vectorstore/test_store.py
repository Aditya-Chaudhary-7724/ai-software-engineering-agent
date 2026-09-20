"""Integration tests against a real PostgreSQL + pgvector instance.

LOCAL TESTED: this dev environment has Postgres 18 with pgvector 0.8.6
installed locally; these tests run against a dedicated `ai_swe_agent_test`
database and clean up after themselves (see conftest.py). They skip
automatically if no reachable Postgres is configured.
"""

from tests.vectorstore.conftest import requires_postgres
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.models import CodeChunk

PROVIDER = DeterministicLocalEmbeddingProvider()


def _chunk(symbol_name: str, content: str) -> CodeChunk:
    return CodeChunk(
        relative_path="main.py",
        language="Python",
        chunk_type="function",
        symbol_name=symbol_name,
        qualified_name=symbol_name,
        start_line=1,
        end_line=2,
        content=content,
    )


@requires_postgres
def test_get_or_create_repository_is_idempotent(vector_store):
    conn = vector_store.connect()
    try:
        first_id = vector_store.get_or_create_repository(conn, "demo", "/tmp/demo")
        second_id = vector_store.get_or_create_repository(conn, "demo-renamed", "/tmp/demo")
        assert first_id == second_id
    finally:
        conn.close()


@requires_postgres
def test_insert_and_exact_match_search_returns_zero_distance(vector_store):
    conn = vector_store.connect()
    try:
        repo_id = vector_store.get_or_create_repository(conn, "demo", "/tmp/demo")
        chunks = [_chunk("add", "def add(a, b):\n    return a + b")]
        embeddings = PROVIDER.embed([c.content for c in chunks])
        vector_store.insert_chunks(conn, repo_id, chunks, embeddings)

        query_embedding = PROVIDER.embed(["def add(a, b):\n    return a + b"])[0]
        results = vector_store.similarity_search(conn, query_embedding, top_k=1)

        assert len(results) == 1
        assert results[0].symbol_name == "add"
        assert results[0].distance == 0.0
    finally:
        conn.close()


@requires_postgres
def test_similarity_search_orders_by_distance(vector_store):
    conn = vector_store.connect()
    try:
        repo_id = vector_store.get_or_create_repository(conn, "demo", "/tmp/demo")
        chunks = [_chunk("add", "def add(a, b): return a + b"), _chunk("sub", "def sub(a, b): return a - b")]
        embeddings = PROVIDER.embed([c.content for c in chunks])
        vector_store.insert_chunks(conn, repo_id, chunks, embeddings)

        query_embedding = PROVIDER.embed(["def add(a, b): return a + b"])[0]
        results = vector_store.similarity_search(conn, query_embedding, top_k=2)

        assert [r.symbol_name for r in results] == ["add", "sub"]
        assert results[0].distance <= results[1].distance
    finally:
        conn.close()


@requires_postgres
def test_similarity_search_metadata_filtering_by_repository(vector_store):
    conn = vector_store.connect()
    try:
        repo_a = vector_store.get_or_create_repository(conn, "repo-a", "/tmp/repo-a")
        repo_b = vector_store.get_or_create_repository(conn, "repo-b", "/tmp/repo-b")

        chunk = _chunk("add", "def add(a, b): return a + b")
        embedding = PROVIDER.embed([chunk.content])
        vector_store.insert_chunks(conn, repo_a, [chunk], embedding)
        vector_store.insert_chunks(conn, repo_b, [chunk], embedding)

        query_embedding = PROVIDER.embed([chunk.content])[0]
        results = vector_store.similarity_search(conn, query_embedding, top_k=10, repository_id=repo_a)

        assert len(results) == 1
    finally:
        conn.close()


@requires_postgres
def test_similarity_search_metadata_filtering_by_chunk_type(vector_store):
    conn = vector_store.connect()
    try:
        repo_id = vector_store.get_or_create_repository(conn, "demo", "/tmp/demo")
        func_chunk = _chunk("add", "def add(a, b): return a + b")
        class_chunk = CodeChunk(
            relative_path="main.py",
            language="Python",
            chunk_type="class",
            symbol_name="Adder",
            qualified_name="Adder",
            start_line=1,
            end_line=3,
            content="class Adder:\n    pass",
        )
        embeddings = PROVIDER.embed([func_chunk.content, class_chunk.content])
        vector_store.insert_chunks(conn, repo_id, [func_chunk, class_chunk], embeddings)

        query_embedding = PROVIDER.embed(["anything"])[0]
        results = vector_store.similarity_search(conn, query_embedding, top_k=10, chunk_type="class")

        assert len(results) == 1
        assert results[0].chunk_type == "class"
    finally:
        conn.close()
