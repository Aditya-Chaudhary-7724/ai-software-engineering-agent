"""Indexing CHECKS the schema version instead of applying the schema.

Regression: IndexingService used to run apply_schema on every index, so a
schema change reached any database (including a developer's or production
one) as a side effect of indexing, bypassing the explicit
backend/scripts/apply_schema.py step. Now it refuses with
SchemaOutOfDateError and leaves the database untouched.

Throwaway databases (fresh_database_url) for everything that changes the
version; the shared test database is never downgraded.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

import vectorstore.migrations as migrations
import vectorstore.service
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.exceptions import SchemaOutOfDateError
from vectorstore.migrations import SCHEMA_PATH, SCHEMA_VERSION, apply_schema_to_database, recorded_schema_version
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore

from tests.vectorstore.conftest import requires_postgres, vector_store  # noqa: F401
from tests.vectorstore.test_migrations import _catalog, fresh_database_url  # noqa: F401

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"

# schema.sql's sha256 for each SCHEMA_VERSION. Changing schema.sql fails the
# first test until SCHEMA_VERSION is bumped and the new hash recorded here:
# that is what makes "indexing refuses an out-of-date schema" trustworthy.
SCHEMA_SHA256_BY_VERSION = {
    1: "7463bcb0816766e5c3b2131b18d95b93c2e5b7ae1fd9cd41ed52d88ed5759e63",
}


def _repo(tmp_path):
    (tmp_path / "calc.py").write_text("def add_numbers(a, b):\n    return a + b\n")
    return str(tmp_path)


def _index(url, tmp_path):
    return IndexingService(DeterministicLocalEmbeddingProvider(), VectorStore(url)).index_repository(_repo(tmp_path))


def _set_version(url, version):
    with psycopg.connect(url) as conn:
        conn.execute("UPDATE schema_version SET version = %s", (version,))


def test_schema_sql_changes_require_a_schema_version_bump():
    digest = hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()
    assert SCHEMA_SHA256_BY_VERSION.get(SCHEMA_VERSION) == digest, (
        "backend/vectorstore/schema.sql changed: bump SCHEMA_VERSION in vectorstore/migrations.py "
        f"and record {digest} for it in this test"
    )


@requires_postgres
def test_indexing_refuses_a_never_migrated_database_and_changes_nothing(fresh_database_url, tmp_path):  # noqa: F811
    before = _catalog(fresh_database_url)

    with pytest.raises(SchemaOutOfDateError, match=r"never applied.*run backend/scripts/apply_schema\.py"):
        _index(fresh_database_url, tmp_path)

    assert _catalog(fresh_database_url) == before  # no extension, no tables: nothing was applied


@requires_postgres
def test_after_the_explicit_step_the_version_is_recorded_and_indexing_works(fresh_database_url, tmp_path):  # noqa: F811
    apply_schema_to_database(fresh_database_url)
    with psycopg.connect(fresh_database_url) as conn:
        assert recorded_schema_version(conn) == SCHEMA_VERSION
    assert _index(fresh_database_url, tmp_path).chunks_indexed == 1


@requires_postgres
def test_an_older_recorded_version_is_refused(fresh_database_url, tmp_path):  # noqa: F811
    apply_schema_to_database(fresh_database_url)
    _set_version(fresh_database_url, SCHEMA_VERSION - 1)
    with pytest.raises(SchemaOutOfDateError) as caught:
        _index(fresh_database_url, tmp_path)
    assert (caught.value.found, caught.value.expected) == (SCHEMA_VERSION - 1, SCHEMA_VERSION)


@requires_postgres
def test_a_newer_recorded_version_is_accepted_and_never_lowered(fresh_database_url, tmp_path):  # noqa: F811
    apply_schema_to_database(fresh_database_url)
    _set_version(fresh_database_url, SCHEMA_VERSION + 5)

    assert _index(fresh_database_url, tmp_path).chunks_indexed == 1  # the schema is additive
    apply_schema_to_database(fresh_database_url)  # e.g. an older image re-applying its schema
    with psycopg.connect(fresh_database_url) as conn:
        assert recorded_schema_version(conn) == SCHEMA_VERSION + 5


@requires_postgres
def test_indexing_never_calls_apply_schema(vector_store, tmp_path, monkeypatch):  # noqa: F811
    def forbidden(*args, **kwargs):
        raise AssertionError("indexing must not apply the schema")

    monkeypatch.setattr(migrations, "apply_schema", forbidden)
    # Also the name inside vectorstore.service, in case it is imported there
    # directly (the old code did exactly that, which a module-only patch misses).
    monkeypatch.setattr(vectorstore.service, "apply_schema", forbidden, raising=False)
    result = IndexingService(DeterministicLocalEmbeddingProvider(), vector_store).index_repository(_repo(tmp_path))
    assert result.chunks_indexed == 1


@requires_postgres
def test_the_deploy_script_records_the_version(fresh_database_url):  # noqa: F811
    result = subprocess.run(
        [sys.executable, str(BACKEND_DIR / "scripts" / "apply_schema.py")],
        cwd=BACKEND_DIR, env={"PATH": "/usr/bin:/bin", "DATABASE_URL": fresh_database_url},
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with psycopg.connect(fresh_database_url) as conn:
        assert recorded_schema_version(conn) == SCHEMA_VERSION
