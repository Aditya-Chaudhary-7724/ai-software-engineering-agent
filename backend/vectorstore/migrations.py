"""Minimal, idempotent schema application.

The schema is small and still actively evolving alongside the rest of
this project, so a full migration framework (e.g. Alembic) would add
ceremony without benefit yet — `CREATE ... IF NOT EXISTS` is sufficient
and honest about the schema's current maturity. Alembic is worth
reconsidering once the schema needs versioned, reversible changes.
"""

from pathlib import Path

import psycopg

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def apply_schema(conn: psycopg.Connection) -> None:
    sql = SCHEMA_PATH.read_text()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
