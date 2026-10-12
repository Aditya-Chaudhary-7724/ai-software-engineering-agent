"""Read-only queries over the Phase 3 `repositories`/`code_chunks` tables
for the HTTP API.

Kept in the API layer rather than added to `VectorStore`: these are
presentation queries (counts, language breakdowns) that no retrieval
code path needs, and `VectorStore`'s interface stays unchanged.

`RepositoryRecord.root_path` is the TRUSTED server-side path, resolved
from a `repository_id` — it is used to scope file/graph access and is
never serialized into a response (see `api/schemas.py`).
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import psycopg

from api.errors import APIError

_CONNECT_TIMEOUT_SECONDS = 5

_REPOSITORY_SUMMARY_COLUMNS = """
    r.id, r.name, r.root_path, r.ingested_at,
    COUNT(c.id) AS chunk_count,
    COUNT(DISTINCT c.relative_path) AS indexed_file_count,
    COALESCE(array_agg(DISTINCT c.language) FILTER (WHERE c.language IS NOT NULL), '{}') AS languages
"""


@dataclass(frozen=True)
class RepositoryRecord:
    id: int
    name: str
    root_path: str
    first_ingested_at: datetime
    chunk_count: int
    indexed_file_count: int
    languages: List[str]
    chunks_by_language: Dict[str, int] = field(default_factory=dict)
    chunks_by_type: Dict[str, int] = field(default_factory=dict)


def _connect(database_url: str) -> psycopg.Connection:
    try:
        return psycopg.connect(database_url, connect_timeout=_CONNECT_TIMEOUT_SECONDS)
    except psycopg.OperationalError:
        # Never surface the driver message: it can include host/user details.
        raise APIError(503, "database_unavailable")


def _record_from_row(row: tuple) -> RepositoryRecord:
    return RepositoryRecord(
        id=row[0],
        name=row[1],
        root_path=row[2],
        first_ingested_at=row[3],
        chunk_count=row[4],
        indexed_file_count=row[5],
        languages=sorted(row[6]),
    )


def _run(database_url: str, operation):
    conn = _connect(database_url)
    try:
        with conn.cursor() as cur:
            return operation(cur)
    except psycopg.errors.UndefinedTable:
        # `apply_schema.py` has not been run against this database yet —
        # an operator setup step, not "zero repositories".
        raise APIError(503, "schema_not_initialized")
    except psycopg.OperationalError:
        raise APIError(503, "database_unavailable")
    finally:
        conn.close()


def list_repositories(database_url: str, limit: int, offset: int) -> Tuple[List[RepositoryRecord], int]:
    def operation(cur) -> Tuple[List[RepositoryRecord], int]:
        cur.execute("SELECT COUNT(*) FROM repositories")
        total = cur.fetchone()[0]
        cur.execute(
            f"""
            SELECT {_REPOSITORY_SUMMARY_COLUMNS}
            FROM repositories r
            LEFT JOIN code_chunks c ON c.repository_id = r.id
            GROUP BY r.id
            ORDER BY r.ingested_at DESC, r.id DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"limit": limit, "offset": offset},
        )
        return [_record_from_row(row) for row in cur.fetchall()], total

    return _run(database_url, operation)


def get_repository(database_url: str, repository_id: int) -> Optional[RepositoryRecord]:
    def operation(cur) -> Optional[RepositoryRecord]:
        cur.execute(
            f"""
            SELECT {_REPOSITORY_SUMMARY_COLUMNS}
            FROM repositories r
            LEFT JOIN code_chunks c ON c.repository_id = r.id
            WHERE r.id = %(id)s
            GROUP BY r.id
            """,
            {"id": repository_id},
        )
        row = cur.fetchone()
        if row is None:
            return None
        base = _record_from_row(row)

        cur.execute(
            "SELECT COALESCE(language, 'unknown'), COUNT(*) FROM code_chunks "
            "WHERE repository_id = %(id)s GROUP BY 1 ORDER BY 2 DESC, 1",
            {"id": repository_id},
        )
        by_language = {language: count for language, count in cur.fetchall()}
        cur.execute(
            "SELECT chunk_type, COUNT(*) FROM code_chunks WHERE repository_id = %(id)s GROUP BY 1 ORDER BY 2 DESC, 1",
            {"id": repository_id},
        )
        by_type = {chunk_type: count for chunk_type, count in cur.fetchall()}

        return replace(base, chunks_by_language=by_language, chunks_by_type=by_type)

    return _run(database_url, operation)
