"""Shared fixtures for graph tests.

Neo4j Community Edition supports only a single database (verified
against this project's local instance — `CREATE DATABASE` is rejected
as an Enterprise-only administration command), so there's no separate
"test" database to isolate into the way tests/vectorstore/ uses a
dedicated Postgres database. Instead, `neo4j_client` scopes all
cleanup precisely to the `tmp_path` each test already gets (used as
the repository root), deleting only nodes tagged with that exact path
— never a broader wipe.
"""

import pytest

from graph.client import Neo4jClient
from graph.exceptions import MissingCredentialsError
from graph.schema import apply_constraints


def _neo4j_available() -> bool:
    try:
        client = Neo4jClient()
    except MissingCredentialsError:
        return False
    try:
        client.verify_connectivity()
        return True
    except Exception:
        return False
    finally:
        client.close()


requires_neo4j = pytest.mark.skipif(
    not _neo4j_available(), reason="Neo4j not reachable (REQUIRES EXTERNAL SERVICE)"
)


@pytest.fixture
def neo4j_client(tmp_path):
    client = Neo4jClient()
    apply_constraints(client)
    root_path = str(tmp_path.resolve())

    yield client

    client.run("MATCH (n {repository_root_path: $root_path}) DETACH DELETE n", root_path=root_path)
    client.run("MATCH (r:Repository {root_path: $root_path}) DETACH DELETE r", root_path=root_path)
    client.close()
