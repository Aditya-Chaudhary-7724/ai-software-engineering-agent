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


def apply_schema_to_database(database_url: str) -> None:
    """Applies the schema to `database_url`, including a completely fresh
    database.

    Uses a PLAIN connection on purpose — never `VectorStore.connect()`,
    whose `register_vector()` looks up the `vector` type and fails on a
    fresh database, before schema.sql's own `CREATE EXTENSION IF NOT EXISTS
    vector` (its first statement) could ever run. Connections opened after
    this returns register vector support normally.

    All of schema.sql runs in ONE transaction (`apply_schema` commits once
    at the end; on any error the connection's context manager rolls back),
    so a failure leaves the database exactly as it was — no extension, no
    half-created tables.
    """
    with psycopg.connect(database_url) as conn:
        apply_schema(conn)
