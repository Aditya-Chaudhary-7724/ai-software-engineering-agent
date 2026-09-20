"""Shared fixtures for vectorstore tests.

Tests that need a real PostgreSQL + pgvector instance are marked with
`requires_postgres` and skip cleanly (rather than failing) when one
isn't reachable, per the "LOCAL TESTED vs REQUIRES EXTERNAL SERVICE"
distinction: this project's dev environment happens to have a local
Postgres with pgvector installed, but that shouldn't be assumed.
"""

import getpass
import os

import psycopg
import pytest

from vectorstore.migrations import apply_schema
from vectorstore.store import VectorStore

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", f"postgresql://{getpass.getuser()}@localhost:5432/ai_swe_agent_test"
)


def _postgres_available() -> bool:
    try:
        conn = psycopg.connect(TEST_DATABASE_URL, connect_timeout=2)
        conn.close()
        return True
    except psycopg.OperationalError:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="PostgreSQL test database not reachable (REQUIRES EXTERNAL SERVICE)",
)


@pytest.fixture
def vector_store():
    store = VectorStore(TEST_DATABASE_URL)
    conn = store.connect()
    apply_schema(conn)
    conn.close()

    yield store

    cleanup_conn = store.connect()
    with cleanup_conn.cursor() as cur:
        cur.execute("TRUNCATE code_chunks, repositories RESTART IDENTITY CASCADE;")
    cleanup_conn.commit()
    cleanup_conn.close()
