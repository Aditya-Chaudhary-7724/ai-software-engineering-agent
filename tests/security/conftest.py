"""Shared fixtures for the security regression suite — re-exports the
same real-service fixtures every other phase's tests already use
(`requires_postgres`, `requires_neo4j`, `requires_docker`), so these
tests exercise the ACTUAL integrated system rather than only mocks,
per this phase's own instruction.
"""

from tests.github_integration.conftest import PUBLIC_TEST_REPO_URL, requires_network
from tests.graph.conftest import neo4j_client, requires_neo4j
from tests.sandbox.conftest import requires_docker, requires_sandbox_image
from tests.vectorstore.conftest import requires_postgres, vector_store

__all__ = [
    "requires_postgres",
    "vector_store",
    "requires_neo4j",
    "neo4j_client",
    "requires_docker",
    "requires_sandbox_image",
    "requires_network",
    "PUBLIC_TEST_REPO_URL",
]
