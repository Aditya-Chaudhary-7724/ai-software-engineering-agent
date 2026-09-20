"""Expands an initial set of vector/keyword hits with structurally
related symbols, using the real relationships Phase 5 built — never a
name-matching guess.

Given a seed symbol (a class or function that already matched via
vector or keyword search), one hop of graph traversal answers "what
else is relevant because of how this is *structured*, not just how it
reads": a class's other methods, its inheritance neighbors, and the
symbols defined in files it depends on (the "login route -> auth
service -> JWT utility" chain from this phase's own example).

Relevance weights below (GRAPH_SCORE_BY_RELATIONSHIP) are a documented
heuristic starting point, not a measured value — see ranking.py's
docstring for the same caveat applied to the overall combined score.
"""

from typing import Optional

import psycopg

from graph.client import Neo4jClient

from hybrid.models import HybridCandidate

GRAPH_SCORE_BY_RELATIONSHIP = {
    "class_method": 0.6,
    "parent_class": 0.6,
    "sibling_method": 0.4,
    "ancestor_class": 0.5,
    "descendant_class": 0.4,
    "dependency_symbol": 0.3,
}


def _get_class_methods(client: Neo4jClient, root_path: str, relative_path: str, class_name: str) -> list[dict]:
    return client.run(
        """
        MATCH (c:Class {repository_root_path: $root_path, relative_path: $relative_path, name: $class_name})
        MATCH (c)-[:DEFINES]->(fn:Function)
        RETURN fn.relative_path AS relative_path, fn.qualified_name AS qualified_name
        """,
        root_path=root_path,
        relative_path=relative_path,
        class_name=class_name,
    )


def _get_parent_class_and_siblings(
    client: Neo4jClient, root_path: str, relative_path: str, method_qualified_name: str
) -> tuple[Optional[dict], list[dict]]:
    rows = client.run(
        """
        MATCH (c:Class {repository_root_path: $root_path, relative_path: $relative_path})
        MATCH (c)-[:DEFINES]->(:Function {qualified_name: $qualified_name})
        MATCH (c)-[:DEFINES]->(sibling:Function)
        WHERE sibling.qualified_name <> $qualified_name
        RETURN c.name AS class_name, sibling.relative_path AS relative_path,
               sibling.qualified_name AS qualified_name
        """,
        root_path=root_path,
        relative_path=relative_path,
        qualified_name=method_qualified_name,
    )
    if not rows:
        return None, []
    parent = {"relative_path": relative_path, "qualified_name": rows[0]["class_name"]}
    siblings = [{"relative_path": r["relative_path"], "qualified_name": r["qualified_name"]} for r in rows]
    return parent, siblings


def _get_inheritance_neighbors(
    client: Neo4jClient, root_path: str, relative_path: str, class_name: str
) -> tuple[list[dict], list[dict]]:
    ancestors = client.run(
        """
        MATCH (c:Class {repository_root_path: $root_path, relative_path: $relative_path, name: $class_name})
        MATCH (c)-[:INHERITS]->(ancestor:Class)
        RETURN ancestor.relative_path AS relative_path, ancestor.name AS qualified_name
        """,
        root_path=root_path,
        relative_path=relative_path,
        class_name=class_name,
    )
    descendants = client.run(
        """
        MATCH (c:Class {repository_root_path: $root_path, relative_path: $relative_path, name: $class_name})
        MATCH (descendant:Class)-[:INHERITS]->(c)
        RETURN descendant.relative_path AS relative_path, descendant.name AS qualified_name
        """,
        root_path=root_path,
        relative_path=relative_path,
        class_name=class_name,
    )
    return ancestors, descendants


def _get_dependency_symbols(client: Neo4jClient, root_path: str, relative_path: str) -> list[dict]:
    return client.run(
        """
        MATCH (f:File {repository_root_path: $root_path, relative_path: $relative_path})
        MATCH (f)-[:IMPORTS]->(:Module)-[:RESOLVES_TO]->(dep:File)
        MATCH (dep)-[:DEFINES]->(symbol)
        WHERE symbol:Class OR symbol:Function
        RETURN dep.relative_path AS relative_path, coalesce(symbol.qualified_name, symbol.name) AS qualified_name
        """,
        root_path=root_path,
        relative_path=relative_path,
    )


def find_related_symbols(
    client: Neo4jClient, root_path: str, relative_path: str, chunk_type: str, symbol_name: str, qualified_name: str
) -> list[tuple[str, str, str]]:
    """Returns (relationship_type, relative_path, qualified_name) tuples
    for symbols one hop away from the given seed in the graph.
    """
    related: list[tuple[str, str, str]] = []

    if chunk_type == "class":
        for row in _get_class_methods(client, root_path, relative_path, symbol_name):
            related.append(("class_method", row["relative_path"], row["qualified_name"]))

        ancestors, descendants = _get_inheritance_neighbors(client, root_path, relative_path, symbol_name)
        for row in ancestors:
            related.append(("ancestor_class", row["relative_path"], row["qualified_name"]))
        for row in descendants:
            related.append(("descendant_class", row["relative_path"], row["qualified_name"]))

    elif chunk_type == "function":
        parent, siblings = _get_parent_class_and_siblings(client, root_path, relative_path, qualified_name)
        if parent:
            related.append(("parent_class", parent["relative_path"], parent["qualified_name"]))
        for row in siblings:
            related.append(("sibling_method", row["relative_path"], row["qualified_name"]))

    for row in _get_dependency_symbols(client, root_path, relative_path):
        related.append(("dependency_symbol", row["relative_path"], row["qualified_name"]))

    return related


def fetch_chunks_by_symbol(
    conn: psycopg.Connection, repository_id: int, pairs: list[tuple[str, str]]
) -> list[dict]:
    """Looks up code_chunks rows matching (relative_path, qualified_name)
    pairs found via graph traversal. Built as a dynamic OR of exact-match
    pairs rather than two independent ANY(...) arrays, since the latter
    would incorrectly match cross-combinations across different pairs.
    """
    if not pairs:
        return []

    conditions = []
    params: dict = {"repository_id": repository_id}
    for i, (relative_path, qualified_name) in enumerate(pairs):
        conditions.append(f"(relative_path = %(path{i})s AND qualified_name = %(qname{i})s)")
        params[f"path{i}"] = relative_path
        params[f"qname{i}"] = qualified_name

    sql = f"""
        SELECT id, relative_path, language, chunk_type, symbol_name, qualified_name,
               start_line, end_line, content
        FROM code_chunks
        WHERE repository_id = %(repository_id)s AND ({" OR ".join(conditions)})
    """

    columns = [
        "id", "relative_path", "language", "chunk_type", "symbol_name",
        "qualified_name", "start_line", "end_line", "content",
    ]
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    return rows


def build_graph_candidates(
    client: Neo4jClient,
    conn: psycopg.Connection,
    repository_id: int,
    root_path: str,
    seed_candidates: list,
) -> list[HybridCandidate]:
    """Runs graph expansion from every seed candidate, resolves the
    results to real code_chunks, and returns them as HybridCandidates
    with a graph_score (vector_similarity=keyword_score=0.0 — see
    ranking.py for how these combine).
    """
    best_relationship_by_key: dict[tuple[str, str], str] = {}

    for seed in seed_candidates:
        if seed.chunk_type not in ("class", "function") or not seed.qualified_name:
            continue
        related = find_related_symbols(
            client, root_path, seed.relative_path, seed.chunk_type, seed.symbol_name or "", seed.qualified_name
        )
        for relationship, relative_path, qualified_name in related:
            key = (relative_path, qualified_name)
            existing = best_relationship_by_key.get(key)
            if existing is None or GRAPH_SCORE_BY_RELATIONSHIP[relationship] > GRAPH_SCORE_BY_RELATIONSHIP[existing]:
                best_relationship_by_key[key] = relationship

    pairs = list(best_relationship_by_key.keys())
    rows = fetch_chunks_by_symbol(conn, repository_id, pairs)

    candidates = []
    for row in rows:
        relationship = best_relationship_by_key[(row["relative_path"], row["qualified_name"])]
        graph_score = GRAPH_SCORE_BY_RELATIONSHIP[relationship]
        candidates.append(
            HybridCandidate(
                chunk_id=row["id"],
                relative_path=row["relative_path"],
                language=row["language"],
                chunk_type=row["chunk_type"],
                symbol_name=row["symbol_name"],
                qualified_name=row["qualified_name"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                content=row["content"],
                vector_similarity=0.0,
                keyword_score=0.0,
                graph_score=graph_score,
                combined_score=0.0,
                found_via=(f"graph:{relationship}",),
            )
        )
    return candidates
