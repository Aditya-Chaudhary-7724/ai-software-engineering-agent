"""Thin wrapper around the official Neo4j driver.

No query-building abstraction on top of Cypher: Cypher is already the
right level of expressiveness for graph queries, and wrapping it would
just be an unjustified layer.
"""

import os
from typing import Any, Optional

from neo4j import GraphDatabase

from graph.exceptions import MissingCredentialsError


class Neo4jClient:
    def __init__(
        self,
        uri: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        resolved_uri = uri or os.environ.get("NEO4J_URI")
        resolved_username = username or os.environ.get("NEO4J_USERNAME")
        resolved_password = password or os.environ.get("NEO4J_PASSWORD")

        if not (resolved_uri and resolved_username and resolved_password):
            raise MissingCredentialsError(
                "NEO4J_URI, NEO4J_USERNAME, and NEO4J_PASSWORD must all be set "
                "(directly or via your local .env) to connect to Neo4j."
            )

        self._driver = GraphDatabase.driver(resolved_uri, auth=(resolved_username, resolved_password))

    def verify_connectivity(self) -> None:
        self._driver.verify_connectivity()

    def run(self, query: str, **params: Any) -> list[dict]:
        with self._driver.session() as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "Neo4jClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
