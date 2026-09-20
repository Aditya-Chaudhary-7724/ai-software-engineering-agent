"""Idempotent constraint/index setup.

Composite uniqueness constraints (verified to work on Neo4j 5
Community Edition, not just Enterprise) key every node type by
(repository root_path, ...) so the same repository can be re-indexed
without creating duplicates, and different repositories never collide.
"""

from graph.client import Neo4jClient

_CONSTRAINTS = [
    "CREATE CONSTRAINT repository_root_path IF NOT EXISTS "
    "FOR (r:Repository) REQUIRE r.root_path IS UNIQUE",
    "CREATE CONSTRAINT folder_key IF NOT EXISTS "
    "FOR (f:Folder) REQUIRE (f.repository_root_path, f.relative_path) IS UNIQUE",
    "CREATE CONSTRAINT file_key IF NOT EXISTS "
    "FOR (f:File) REQUIRE (f.repository_root_path, f.relative_path) IS UNIQUE",
    "CREATE CONSTRAINT class_key IF NOT EXISTS "
    "FOR (c:Class) REQUIRE (c.repository_root_path, c.relative_path, c.name) IS UNIQUE",
    "CREATE CONSTRAINT function_key IF NOT EXISTS "
    "FOR (fn:Function) REQUIRE (fn.repository_root_path, fn.relative_path, fn.qualified_name) IS UNIQUE",
    "CREATE CONSTRAINT module_key IF NOT EXISTS "
    "FOR (m:Module) REQUIRE (m.repository_root_path, m.name) IS UNIQUE",
]


def apply_constraints(client: Neo4jClient) -> None:
    for statement in _CONSTRAINTS:
        client.run(statement)
