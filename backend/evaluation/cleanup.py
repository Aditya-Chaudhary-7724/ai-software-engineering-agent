"""F7: the ONLY way evaluation runners delete PostgreSQL rows or Neo4j nodes.

Every evaluation repository lives in a fresh temporary directory (prefix
`eval-`), indexed by that unique path: `repositories.root_path` in
PostgreSQL, and the `repository_root_path` / `root_path` key that every
Neo4j node the graph builder creates carries. That path is the namespace.

Before deleting, each helper checks that the namespace really is a
disposable temporary directory (`require_disposable_root`), reading the
root path back from the database rather than trusting the caller. So a bug
that passes a development repository's id or path raises
`UnsafeEvaluationTargetError` instead of deleting real data. Nothing here
truncates a table or deletes by anything broader than one namespace.

Limitation (made explicit, tested in tests/evaluation/test_f7_db_isolation.py):
cleanup runs in `finally` blocks, so an exception inside a case still cleans
up, and so does Ctrl-C (KeyboardInterrupt) and — once the CLI has called
`exit_on_sigterm` — SIGTERM. A process that is KILLED (SIGKILL, power
loss), or SIGTERMed without that handler, cannot. Its rows and
nodes stay behind (and so does its temporary directory), namespaced under an
`eval-` temporary path. A finished run has already removed its own data, so
`find_leftover_*` — whose counts every evaluation report prints — finds
only what killed runs (or an evaluation running at the same moment) left.
`remove_leftovers` (CLI: `run_evaluation.py --remove-leftovers`) deletes
them through the same guard; run it only when no evaluation is running.
"""

import signal
import sys
import tempfile
from pathlib import Path
from typing import Any, List, Optional

from vectorstore.store import VectorStore

from evaluation.safety import require_disposable_root

EVALUATION_PREFIX = "eval-"


def exit_on_sigterm() -> None:
    """Python's default SIGTERM action ends the process without running
    `finally` blocks, so `kill <pid>` (or a CI job cancellation) would skip
    cleanup exactly like SIGKILL. Raising SystemExit instead unwinds the
    stack, so every runner's `finally` cleanup runs. Called by the
    evaluation CLI only — never by the application."""

    def _raise_system_exit(signum, frame):
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, _raise_system_exit)


def delete_evaluation_repository(vector_store: VectorStore, repository_id: int) -> None:
    """Deletes one evaluation repository's chunks and row, after checking —
    from the row itself — that it belongs to a temporary directory."""
    conn = vector_store.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT root_path FROM repositories WHERE id = %s FOR UPDATE", (repository_id,))
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                return
            require_disposable_root(row[0])
            cur.execute("DELETE FROM code_chunks WHERE repository_id = %s", (repository_id,))
            cur.execute("DELETE FROM repositories WHERE id = %s", (repository_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_evaluation_graph(neo4j_client: Any, root_path: str) -> None:
    """Deletes one evaluation repository's Neo4j namespace."""
    require_disposable_root(root_path)
    neo4j_client.run("MATCH (n {repository_root_path: $root_path}) DETACH DELETE n", root_path=root_path)
    neo4j_client.run("MATCH (r:Repository {root_path: $root_path}) DETACH DELETE r", root_path=root_path)


def _is_evaluation_namespace(root_path: str) -> bool:
    path = Path(root_path)
    temp_root = Path(tempfile.gettempdir()).resolve()
    return temp_root in path.resolve().parents and path.name.startswith(EVALUATION_PREFIX)


def find_leftover_repositories(vector_store: VectorStore) -> List[int]:
    """Ids of evaluation repositories still in the database (an `eval-`
    directory under the system temporary directory)."""
    conn = vector_store.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, root_path FROM repositories WHERE root_path LIKE %s", (f"%/{EVALUATION_PREFIX}%",))
            return [repository_id for repository_id, root_path in cur.fetchall() if _is_evaluation_namespace(root_path)]
    finally:
        conn.close()


def find_leftover_graph_roots(neo4j_client: Any) -> List[str]:
    rows = neo4j_client.run(
        "MATCH (r:Repository) WHERE r.root_path CONTAINS $marker RETURN r.root_path AS root_path",
        marker=f"/{EVALUATION_PREFIX}",
    )
    return [row["root_path"] for row in rows if _is_evaluation_namespace(row["root_path"])]


def remove_leftovers(vector_store: VectorStore, neo4j_client: Optional[Any] = None) -> dict:
    """Deletes what `find_leftover_*` reports, through the guarded helpers."""
    repositories = find_leftover_repositories(vector_store)
    for repository_id in repositories:
        delete_evaluation_repository(vector_store, repository_id)
    roots = find_leftover_graph_roots(neo4j_client) if neo4j_client is not None else []
    for root_path in roots:
        delete_evaluation_graph(neo4j_client, root_path)
    return {"repositories": len(repositories), "graph_repositories": len(roots)}
