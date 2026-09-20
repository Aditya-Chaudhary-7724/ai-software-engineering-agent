"""Re-exports the Postgres fixtures from tests/vectorstore/conftest.py
so RAG's integration tests use the exact same dedicated test database
and cleanup behavior, instead of duplicating that setup.
"""

from tests.vectorstore.conftest import TEST_DATABASE_URL, requires_postgres, vector_store

__all__ = ["TEST_DATABASE_URL", "requires_postgres", "vector_store"]
