"""Schema application on a completely fresh PostgreSQL database.

Regression for: `backend/scripts/apply_schema.py` opened its connection
with `VectorStore.connect()`, whose pgvector `register_vector()` needs the
`vector` type, which only schema.sql's own `CREATE EXTENSION` creates. On a
fresh database it failed with "vector type not found in the database"
before any schema SQL ran, so a first deploy needed a manual workaround.

Every test gets its OWN throwaway database, created on the
TEST_DATABASE_URL server from `template0` (so it cannot inherit an
extension someone installed in `template1`) under a random
`ai_swe_agent_fresh_<hex>` name, and dropped afterwards. The development
and application databases are never connected to.
"""

import re
import subprocess
import sys
import uuid
from pathlib import Path

import numpy as np
import psycopg
import pytest
from psycopg import sql

from tests.vectorstore.conftest import TEST_DATABASE_URL, requires_postgres
from vectorstore import migrations
from vectorstore.migrations import SCHEMA_PATH, apply_schema_to_database
from vectorstore.store import VectorStore

REPO_ROOT = Path(__file__).resolve().parents[2]
FRESH_PREFIX = "ai_swe_agent_fresh_"
EXPECTED_TABLES = {"repositories", "code_chunks", "jobs", "agent_tasks"}
CHECKPOINT_TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}


def _url_for(dbname: str) -> str:
    info = psycopg.conninfo.conninfo_to_dict(TEST_DATABASE_URL)
    info["dbname"] = dbname
    return psycopg.conninfo.make_conninfo(**{k: str(v) for k, v in info.items() if v is not None})


@pytest.fixture
def fresh_database_url():
    name = FRESH_PREFIX + uuid.uuid4().hex[:12]
    try:
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
    except psycopg.errors.InsufficientPrivilege:
        pytest.skip("role may not CREATE DATABASE; a fresh database cannot be created")
    try:
        yield _url_for(name)
    finally:
        assert name.startswith(FRESH_PREFIX)  # only ever drop what this fixture created
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def _catalog(url: str):
    """Everything schema application can create: extensions, and every
    table/index/sequence in `public` with its definition."""
    with psycopg.connect(url) as conn:
        extensions = conn.execute("SELECT extname, extversion FROM pg_extension ORDER BY 1").fetchall()
        relations = conn.execute(
            "SELECT c.relname, c.relkind, coalesce(pg_get_indexdef(c.oid), '') FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' ORDER BY 1"
        ).fetchall()
    return extensions, relations


def _tables(url: str) -> set:
    return {name for name, kind, _ in _catalog(url)[1] if kind == "r"}


def _has_vector(url: str) -> bool:
    return any(name == "vector" for name, _ in _catalog(url)[0])


def _run_apply_schema_script(url: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "backend" / "scripts" / "apply_schema.py")],
        env={"PATH": "/usr/bin:/bin", "DATABASE_URL": url},
        capture_output=True, text=True, timeout=120,
    )


def test_schema_sql_creates_the_vector_extension_before_any_other_statement():
    without_comments = re.sub(r"--[^\n]*", "", SCHEMA_PATH.read_text())
    statements = [s.strip() for s in without_comments.split(";") if s.strip()]
    assert statements[0] == "CREATE EXTENSION IF NOT EXISTS vector"


@requires_postgres
def test_the_fresh_database_really_is_fresh_and_vector_registration_fails_there(fresh_database_url):
    """The root cause, pinned: registration needs the type the schema creates."""
    assert not _has_vector(fresh_database_url)
    assert _tables(fresh_database_url) == set()
    with pytest.raises(psycopg.ProgrammingError, match="vector type not found"):
        VectorStore(fresh_database_url).connect()


@requires_postgres
def test_a_fresh_database_is_fully_initialised(fresh_database_url):
    apply_schema_to_database(fresh_database_url)

    assert _has_vector(fresh_database_url)
    assert EXPECTED_TABLES <= _tables(fresh_database_url)


@requires_postgres
def test_normal_connections_register_vector_support_after_initialisation(fresh_database_url):
    apply_schema_to_database(fresh_database_url)

    conn = VectorStore(fresh_database_url).connect()  # register_vector() now succeeds
    try:
        value = conn.execute("SELECT %s::vector", (np.array([1.0, 2.0, 3.0]),)).fetchone()[0]
    finally:
        conn.close()
    assert isinstance(value, np.ndarray)  # adapted by the registered type, not returned as text
    assert value.tolist() == [1.0, 2.0, 3.0]


@requires_postgres
def test_reapplying_the_schema_changes_and_duplicates_nothing(fresh_database_url):
    apply_schema_to_database(fresh_database_url)
    first = _catalog(fresh_database_url)

    apply_schema_to_database(fresh_database_url)

    assert _catalog(fresh_database_url) == first


@requires_postgres
def test_an_existing_database_keeps_its_data_when_the_schema_is_reapplied(fresh_database_url):
    apply_schema_to_database(fresh_database_url)
    with psycopg.connect(fresh_database_url) as conn:
        conn.execute("INSERT INTO repositories (name, root_path) VALUES ('existing', '/srv/existing')")

    apply_schema_to_database(fresh_database_url)

    with psycopg.connect(fresh_database_url) as conn:
        assert conn.execute("SELECT name, root_path FROM repositories").fetchall() == [("existing", "/srv/existing")]


@requires_postgres
def test_a_failure_part_way_through_leaves_a_fresh_database_untouched(fresh_database_url, tmp_path, monkeypatch):
    """The whole schema is one transaction: a statement failing AFTER the
    extension and every table were created rolls all of them back."""
    broken = tmp_path / "schema.sql"
    broken.write_text(SCHEMA_PATH.read_text() + "\nCREATE TABLE repositories (id INT);\n")  # duplicate: fails last
    monkeypatch.setattr(migrations, "SCHEMA_PATH", broken)

    with pytest.raises(psycopg.errors.DuplicateTable):
        apply_schema_to_database(fresh_database_url)

    assert not _has_vector(fresh_database_url)
    assert _tables(fresh_database_url) == set()

    monkeypatch.setattr(migrations, "SCHEMA_PATH", SCHEMA_PATH)  # and a later, correct run still succeeds
    apply_schema_to_database(fresh_database_url)
    assert EXPECTED_TABLES <= _tables(fresh_database_url)


@requires_postgres
def test_the_deploy_script_initialises_a_fresh_database_end_to_end(fresh_database_url):
    """The real deploy step, as a subprocess, on a fresh database: no manual
    CREATE EXTENSION beforehand. Run twice: idempotent."""
    first = _run_apply_schema_script(fresh_database_url)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "Schema applied" in first.stdout
    assert (EXPECTED_TABLES | CHECKPOINT_TABLES) <= _tables(fresh_database_url)
    catalog = _catalog(fresh_database_url)

    second = _run_apply_schema_script(fresh_database_url)
    assert second.returncode == 0, second.stdout + second.stderr
    assert _catalog(fresh_database_url) == catalog

    VectorStore(fresh_database_url).connect().close()
