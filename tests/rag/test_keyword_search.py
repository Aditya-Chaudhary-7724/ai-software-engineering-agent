"""LOCAL TESTED against real PostgreSQL full-text search; skips cleanly
if unreachable (see tests/vectorstore/conftest.py).
"""

from tests.rag.conftest import requires_postgres
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.models import CodeChunk

from rag.keyword_search import keyword_search

PROVIDER = DeterministicLocalEmbeddingProvider()


@requires_postgres
def test_keyword_search_finds_exact_identifier_match(vector_store):
    conn = vector_store.connect()
    try:
        repo_id = vector_store.get_or_create_repository(conn, "demo", "/tmp/demo")
        chunks = [
            CodeChunk(
                relative_path="auth.py",
                language="Python",
                chunk_type="function",
                symbol_name="authenticate_user",
                qualified_name="authenticate_user",
                start_line=1,
                end_line=3,
                content="def authenticate_user(username, password):\n    return check(username, password)",
            ),
            CodeChunk(
                relative_path="math_utils.py",
                language="Python",
                chunk_type="function",
                symbol_name="add",
                qualified_name="add",
                start_line=1,
                end_line=2,
                content="def add(a, b):\n    return a + b",
            ),
        ]
        embeddings = PROVIDER.embed([c.content for c in chunks])
        vector_store.insert_chunks(conn, repo_id, chunks, embeddings)

        results = keyword_search(conn, "authenticate", top_k=5)

        assert len(results) == 1
        assert results[0].symbol_name == "authenticate_user"
        assert results[0].keyword_score > 0
    finally:
        conn.close()


@requires_postgres
def test_keyword_search_respects_metadata_filters(vector_store):
    conn = vector_store.connect()
    try:
        repo_a = vector_store.get_or_create_repository(conn, "repo-a", "/tmp/repo-a")
        repo_b = vector_store.get_or_create_repository(conn, "repo-b", "/tmp/repo-b")

        chunk = CodeChunk(
            relative_path="auth.py",
            language="Python",
            chunk_type="function",
            symbol_name="authenticate_user",
            qualified_name="authenticate_user",
            start_line=1,
            end_line=2,
            content="def authenticate_user(): pass",
        )
        embedding = PROVIDER.embed([chunk.content])
        vector_store.insert_chunks(conn, repo_a, [chunk], embedding)
        vector_store.insert_chunks(conn, repo_b, [chunk], embedding)

        results = keyword_search(conn, "authenticate", top_k=10, repository_id=repo_a)

        assert len(results) == 1
    finally:
        conn.close()


@requires_postgres
def test_keyword_search_no_match_returns_empty(vector_store):
    conn = vector_store.connect()
    try:
        repo_id = vector_store.get_or_create_repository(conn, "demo", "/tmp/demo")
        chunk = CodeChunk(
            relative_path="main.py",
            language="Python",
            chunk_type="function",
            symbol_name="add",
            qualified_name="add",
            start_line=1,
            end_line=2,
            content="def add(a, b):\n    return a + b",
        )
        embedding = PROVIDER.embed([chunk.content])
        vector_store.insert_chunks(conn, repo_id, [chunk], embedding)

        results = keyword_search(conn, "nonexistent_zzz_term", top_k=5)

        assert results == []
    finally:
        conn.close()
