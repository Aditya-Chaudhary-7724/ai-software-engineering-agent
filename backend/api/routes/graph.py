"""Knowledge graph (read-only): summary (F1) and exploration (F5).

Neo4j is OPTIONAL (backend/config.py): "not configured" and
"unreachable" are reported as distinct 503 codes so the UI can explain
which one applies, never as an empty graph.

F5 exploration is bounded and parameterized end to end: the client
supplies an opaque node id (validated against Neo4j's element-id format),
a search string, and a limit (bounded here) — never Cypher, labels, or
relationship types. Queries are fixed templates in graph/queries.py,
scoped to the repository's trusted root_path. Returned nodes carry only an
allowlisted set of properties, so server paths never leave the backend.
"""

import re
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from graph.client import Neo4jClient
from graph.exceptions import MissingCredentialsError
from graph.queries import (
    EXPLORABLE_RELATIONSHIPS,
    get_node_neighborhood,
    get_repository_node_id,
    get_repository_summary,
    repository_exists,
    search_graph_nodes,
)

from api.dependencies import get_neo4j_client_factory, get_repository_record
from api.errors import APIError
from api.repository_catalog import RepositoryRecord
from api.schemas import GraphEdge, GraphNeighborhood, GraphNode, GraphSearchResults, GraphSummary

router = APIRouter(tags=["graph"])


@router.get("/repositories/{repository_id}/graph/summary", response_model=GraphSummary)
def get_graph_summary(
    record: RepositoryRecord = Depends(get_repository_record),
    client_factory: Callable[[], Neo4jClient] = Depends(get_neo4j_client_factory),
) -> GraphSummary:
    try:
        client = client_factory()
    except MissingCredentialsError:
        raise APIError(503, "graph_not_configured")

    try:
        # Both Postgres (ingestion's resolved root) and Neo4j
        # (GraphBuilder resolves the same path) key a repository by the
        # same absolute root_path — the trusted value from the id lookup.
        built = repository_exists(client, record.root_path)
        counts = get_repository_summary(client, record.root_path) if built else {}
    except Exception:
        # Driver errors (ServiceUnavailable, AuthError, ...) can carry
        # connection details; report a fixed code only.
        raise APIError(503, "graph_unavailable")
    finally:
        client.close()

    return GraphSummary(
        repository_id=record.id,
        built=built,
        folders=counts.get("folders", 0),
        files=counts.get("files", 0),
        classes=counts.get("classes", 0),
        functions=counts.get("functions", 0),
    )


# --- F5: exploration --------------------------------------------------------

MAX_NEIGHBORHOOD_NODES = 200
DEFAULT_NEIGHBORHOOD_NODES = 100
MAX_SEARCH_RESULTS = 50
# Neo4j 5 element ids look like "4:<database uuid>:<integer>".
_ELEMENT_ID = re.compile(r"^\d{1,3}:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:\d{1,19}$")
_KINDS = ("Repository", "Folder", "File", "Class", "Function", "Module")


def _to_node(node_id: str, labels: List[str], props: Dict[str, Any]) -> Optional[GraphNode]:
    kind = next((label for label in labels if label in _KINDS), None)
    if kind is None:
        return None

    def text(key: str) -> Optional[str]:
        value = props.get(key)
        return value if isinstance(value, str) else None

    def integer(key: str) -> Optional[int]:
        value = props.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    relative_path = text("relative_path")
    if kind == "Repository":
        label = text("name") or "repository"
    elif kind == "Folder":
        label = (relative_path or "").rsplit("/", 1)[-1] or "folder"
    elif kind == "File":
        label = text("filename") or (relative_path or "").rsplit("/", 1)[-1]
    elif kind == "Function":
        label = text("qualified_name") or text("name") or "function"
    else:
        label = text("name") or kind.lower()

    return GraphNode(
        id=node_id,
        kind=kind,
        label=label,
        relative_path=relative_path if kind != "Repository" else None,
        qualified_name=text("qualified_name") if kind == "Function" else None,
        start_line=integer("start_line") if kind in ("Class", "Function") else None,
        end_line=integer("end_line") if kind in ("Class", "Function") else None,
        language=text("language") if kind == "File" else None,
    )


def _run_graph_query(client_factory: Callable[[], Neo4jClient], operation):
    try:
        client = client_factory()
    except MissingCredentialsError:
        raise APIError(503, "graph_not_configured")
    try:
        return operation(client)
    except APIError:
        raise
    except Exception:
        raise APIError(503, "graph_unavailable")
    finally:
        client.close()


@router.get("/repositories/{repository_id}/graph/neighborhood", response_model=GraphNeighborhood)
def get_graph_neighborhood(
    node: Optional[str] = Query(None, max_length=100),
    limit: int = Query(DEFAULT_NEIGHBORHOOD_NODES, ge=1, le=MAX_NEIGHBORHOOD_NODES),
    record: RepositoryRecord = Depends(get_repository_record),
    client_factory: Callable[[], Neo4jClient] = Depends(get_neo4j_client_factory),
) -> GraphNeighborhood:
    """One-hop neighbourhood of `node` (default: the repository node)."""
    if node is not None and not _ELEMENT_ID.match(node):
        raise APIError(400, "invalid_node_id")

    def operation(client: Neo4jClient):
        focus_id = node or get_repository_node_id(client, record.root_path)
        if focus_id is None:
            raise APIError(404, "graph_not_built")
        return get_node_neighborhood(client, record.root_path, focus_id, limit)

    row = _run_graph_query(client_factory, operation)
    if row is None:
        raise APIError(404, "node_not_found")

    focus = _to_node(row["id"], row["labels"], row["properties"])
    if focus is None:
        raise APIError(404, "node_not_found")
    nodes: Dict[str, GraphNode] = {focus.id: focus}
    edges: Dict[str, GraphEdge] = {}
    for item in row["neighbours"]:
        neighbour = _to_node(item["node_id"], item["labels"], item["properties"])
        if neighbour is None or item["type"] not in EXPLORABLE_RELATIONSHIPS:
            continue
        nodes.setdefault(neighbour.id, neighbour)
        edges.setdefault(
            item["relationship_id"],
            GraphEdge(id=item["relationship_id"], source=item["source"], target=item["target"], type=item["type"]),
        )

    return GraphNeighborhood(
        repository_id=record.id,
        focus=focus,
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        total_neighbors=row["total"],
        truncated=row["total"] > len(row["neighbours"]),
        limit=limit,
        relationship_types=list(EXPLORABLE_RELATIONSHIPS),
    )


@router.get("/repositories/{repository_id}/graph/search", response_model=GraphSearchResults)
def search_graph(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=MAX_SEARCH_RESULTS),
    record: RepositoryRecord = Depends(get_repository_record),
    client_factory: Callable[[], Neo4jClient] = Depends(get_neo4j_client_factory),
) -> GraphSearchResults:
    text = q.strip()
    if not text or any(ord(ch) < 32 for ch in text):
        raise APIError(422, "invalid_query")

    def operation(client: Neo4jClient):
        if not repository_exists(client, record.root_path):
            raise APIError(404, "graph_not_built")
        return search_graph_nodes(client, record.root_path, text, limit)

    rows = _run_graph_query(client_factory, operation)
    results = [n for n in (_to_node(r["id"], r["labels"], r["properties"]) for r in rows) if n is not None]
    return GraphSearchResults(repository_id=record.id, query=text, results=results, limit=limit)
