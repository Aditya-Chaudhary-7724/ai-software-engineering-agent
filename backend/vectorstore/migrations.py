"""Minimal, idempotent schema application.

The schema is small and still actively evolving alongside the rest of
this project, so a full migration framework (e.g. Alembic) would add
ceremony without benefit yet — `CREATE ... IF NOT EXISTS` is sufficient
and honest about the schema's current maturity. Alembic is worth
reconsidering once the schema needs versioned, reversible changes.
"""

from pathlib import Path
from typing import Optional

import psycopg

from vectorstore.exceptions import SchemaOutOfDateError

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Bump whenever schema.sql changes (tests/vectorstore/test_schema_version.py
# pins schema.sql's hash to this number and fails until both are updated).
#   1 (2026-11-12): first versioned schema (includes embedding_384,
#     repositories.embedding_model, schema_version).
SCHEMA_VERSION = 1


def apply_schema(conn: psycopg.Connection) -> None:
    """Applies schema.sql and records SCHEMA_VERSION, in one transaction.
    The recorded version never goes DOWN: an older code version re-applying
    its (additive) schema leaves a newer recorded version in place."""
    sql = SCHEMA_PATH.read_text()
    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            """
            INSERT INTO schema_version (singleton, version) VALUES (TRUE, %(version)s)
            ON CONFLICT (singleton) DO UPDATE
                SET version = GREATEST(schema_version.version, EXCLUDED.version), applied_at = now()
            """,
            {"version": SCHEMA_VERSION},
        )
    conn.commit()


def recorded_schema_version(conn: psycopg.Connection) -> Optional[int]:
    """The version apply_schema recorded, or None if it never ran on this
    database (no schema_version table). Read-only; leaves no transaction
    aborted (the table's existence is checked first)."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('schema_version') IS NOT NULL")
        row = cur.fetchone()
        if not row or not row[0]:
            return None
        cur.execute("SELECT version FROM schema_version WHERE singleton")
        version_row = cur.fetchone()
    return int(version_row[0]) if version_row else None


def require_current_schema(conn: psycopg.Connection) -> None:
    """Raises SchemaOutOfDateError unless the database is at SCHEMA_VERSION
    or newer. Never changes the database."""
    found = recorded_schema_version(conn)
    if found is None or found < SCHEMA_VERSION:
        raise SchemaOutOfDateError(found, SCHEMA_VERSION)


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
