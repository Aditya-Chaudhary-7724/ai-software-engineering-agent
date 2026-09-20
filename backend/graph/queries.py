"""Read-only Cypher queries the graph enables that vector search alone
cannot answer reliably: traversal-based questions (what does X depend
on, what inherits from Y, who imports Z) rather than "what looks
semantically similar to this text".
"""

from graph.client import Neo4jClient


def get_repository_summary(client: Neo4jClient, root_path: str) -> dict:
    """Actual node/relationship counts currently in the graph for this
    repository — queried live, not the builder's in-memory tally, so
    this also verifies the write actually persisted.
    """
    node_rows = client.run(
        """
        MATCH (r:Repository {root_path: $root_path})
        OPTIONAL MATCH (r)-[:CONTAINS*0..]->(n)
        WHERE n:Folder OR n:File
        WITH r, collect(DISTINCT n) AS structural
        OPTIONAL MATCH (r)-[:CONTAINS*0..]->(:File)-[:DEFINES]->(c:Class)
        OPTIONAL MATCH (r)-[:CONTAINS*0..]->(:File)-[:DEFINES]->(fn:Function)
        OPTIONAL MATCH (r)-[:CONTAINS*0..]->(:File)-[:DEFINES]->(:Class)-[:DEFINES]->(m:Function)
        RETURN
            size([x IN structural WHERE 'Folder' IN labels(x)]) AS folders,
            size([x IN structural WHERE 'File' IN labels(x)]) AS files,
            count(DISTINCT c) AS classes,
            count(DISTINCT fn) + count(DISTINCT m) AS functions
        """,
        root_path=root_path,
    )
    return node_rows[0] if node_rows else {"folders": 0, "files": 0, "classes": 0, "functions": 0}


def get_file_dependencies(client: Neo4jClient, root_path: str, relative_path: str) -> list[dict]:
    """What a file imports, and which of those imports resolved to a
    real file in this repository (vs. an external package/module).
    """
    return client.run(
        """
        MATCH (f:File {repository_root_path: $root_path, relative_path: $relative_path})-[:IMPORTS]->(m:Module)
        OPTIONAL MATCH (m)-[:RESOLVES_TO]->(resolved:File)
        RETURN m.name AS module, resolved.relative_path AS resolved_file
        ORDER BY module
        """,
        root_path=root_path,
        relative_path=relative_path,
    )


def get_importers_of_file(client: Neo4jClient, root_path: str, relative_path: str) -> list[dict]:
    """Reverse dependency lookup: which files import this one (only
    meaningful for imports Phase 5 could resolve — see resolution.py).
    """
    return client.run(
        """
        MATCH (importer:File {repository_root_path: $root_path})-[:IMPORTS]->(:Module)-[:RESOLVES_TO]->
              (:File {repository_root_path: $root_path, relative_path: $relative_path})
        RETURN DISTINCT importer.relative_path AS importer
        ORDER BY importer
        """,
        root_path=root_path,
        relative_path=relative_path,
    )


def get_class_ancestors(client: Neo4jClient, root_path: str, relative_path: str, class_name: str) -> list[dict]:
    """Full inheritance chain (transitive), not just the direct base class."""
    return client.run(
        """
        MATCH (c:Class {repository_root_path: $root_path, relative_path: $relative_path, name: $class_name})
        MATCH path = (c)-[:INHERITS*1..]->(ancestor:Class)
        RETURN ancestor.name AS name, ancestor.relative_path AS relative_path, length(path) AS distance
        ORDER BY distance
        """,
        root_path=root_path,
        relative_path=relative_path,
        class_name=class_name,
    )


def find_symbol(client: Neo4jClient, root_path: str, name: str) -> list[dict]:
    """Locate every class or function/method in the repository with this
    exact name — a graph-native complement to Phase 4's semantic search
    when the user already knows the identifier they're looking for.
    """
    return client.run(
        """
        MATCH (r:Repository {root_path: $root_path})
        OPTIONAL MATCH (r)-[:CONTAINS*0..]->(:File)-[:DEFINES]->(c:Class {name: $name})
        OPTIONAL MATCH (r)-[:CONTAINS*0..]->(:File)-[:DEFINES]->(fn:Function {name: $name})
        OPTIONAL MATCH (r)-[:CONTAINS*0..]->(:File)-[:DEFINES]->(:Class)-[:DEFINES]->(m:Function {name: $name})
        WITH collect(DISTINCT c) + collect(DISTINCT fn) + collect(DISTINCT m) AS matches
        UNWIND matches AS match
        WITH DISTINCT match WHERE match IS NOT NULL
        RETURN labels(match)[0] AS kind, match.relative_path AS relative_path,
               coalesce(match.qualified_name, match.name) AS name
        ORDER BY relative_path, name
        """,
        root_path=root_path,
        name=name,
    )
