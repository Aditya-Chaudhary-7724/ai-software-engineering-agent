"""Read-only Cypher queries the graph enables that vector search alone
cannot answer reliably: traversal-based questions (what does X depend
on, what inherits from Y, who imports Z) rather than "what looks
semantically similar to this text".
"""

from typing import Optional

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


def repository_exists(client: Neo4jClient, root_path: str) -> bool:
    """Whether a graph was ever built for this repository —
    `get_repository_summary` alone returns all-zero counts both for
    "never built" and for "built, but empty", which a caller (e.g. a UI)
    needs to tell apart.
    """
    rows = client.run("MATCH (r:Repository {root_path: $root_path}) RETURN count(r) AS n", root_path=root_path)
    return bool(rows and rows[0]["n"])


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


# --- Frontend phase F5: bounded, read-only exploration queries -------------
#
# Fixed Cypher templates only: callers supply VALUES (a node element id, a
# search string, a limit), never query text, labels, or relationship types.
# Every query is scoped to one repository by its trusted root_path, so an
# element id from another repository simply matches nothing. Relationship
# types are exactly those GraphBuilder creates — there is no CALLS edge,
# because call sites are never extracted (see module docstring above).

EXPLORABLE_RELATIONSHIPS = ("CONTAINS", "DEFINES", "IMPORTS", "RESOLVES_TO", "INHERITS")
_RELATIONSHIP_PATTERN = "|".join(EXPLORABLE_RELATIONSHIPS)
_IN_REPOSITORY = "({var}.repository_root_path = $root_path OR ({var}:Repository AND {var}.root_path = $root_path))"


def get_repository_node_id(client: Neo4jClient, root_path: str) -> Optional[str]:
    rows = client.run("MATCH (r:Repository {root_path: $root_path}) RETURN elementId(r) AS id", root_path=root_path)
    return rows[0]["id"] if rows else None


def get_node_neighborhood(client: Neo4jClient, root_path: str, node_id: str, limit: int) -> Optional[dict]:
    """One node plus up to `limit` one-hop neighbours (both directions)
    across EXPLORABLE_RELATIONSHIPS, in a stable order, with the total
    neighbour count so callers can say the result was truncated.
    Returns None when the node is not in this repository."""
    rows = client.run(
        f"""
        MATCH (f) WHERE elementId(f) = $node_id AND {_IN_REPOSITORY.format(var='f')}
        CALL (f) {{
            OPTIONAL MATCH (f)-[r:{_RELATIONSHIP_PATTERN}]-(n)
            WHERE {_IN_REPOSITORY.format(var='n')}
            RETURN count(r) AS total
        }}
        CALL (f) {{
            OPTIONAL MATCH (f)-[r:{_RELATIONSHIP_PATTERN}]-(n)
            WHERE {_IN_REPOSITORY.format(var='n')}
            WITH r, n
            ORDER BY type(r), coalesce(n.relative_path, n.name), coalesce(n.qualified_name, n.name)
            LIMIT $limit
            RETURN collect(CASE WHEN r IS NULL THEN NULL ELSE {{
                relationship_id: elementId(r), type: type(r),
                source: elementId(startNode(r)), target: elementId(endNode(r)),
                node_id: elementId(n), labels: labels(n), properties: properties(n)
            }} END) AS neighbours
        }}
        RETURN elementId(f) AS id, labels(f) AS labels, properties(f) AS properties, total, neighbours
        """,
        node_id=node_id,
        root_path=root_path,
        limit=limit,
    )
    return rows[0] if rows else None


def search_graph_nodes(client: Neo4jClient, root_path: str, text: str, limit: int) -> list[dict]:
    """Case-insensitive substring search over class/function/module names
    and file paths in one repository; exact matches first, then prefixes."""
    return client.run(
        """
        MATCH (n) WHERE n.repository_root_path = $root_path AND (n:Class OR n:Function OR n:File OR n:Module)
        WITH n, toLower(CASE WHEN n:File THEN n.relative_path ELSE coalesce(n.qualified_name, n.name) END) AS key,
             toLower(coalesce(n.name, n.filename, '')) AS short, toLower($text) AS needle
        WHERE key CONTAINS needle
        RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS properties
        ORDER BY CASE WHEN short = needle THEN 0 WHEN short STARTS WITH needle THEN 1 ELSE 2 END, size(key), key
        LIMIT $limit
        """,
        root_path=root_path,
        text=text,
        limit=limit,
    )
