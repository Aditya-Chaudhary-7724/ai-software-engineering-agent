"""Tests for the Phase 15 deployment scripts
(`backend/scripts/check_production_config.py`,
`backend/scripts/apply_schema.py`).

Both are invoked as real subprocesses (matching this project's existing
pattern for testing scripts that own `sys.exit`/`main()` behavior — see
`tests/github_integration/test_git_operations.py`), so the assertions
cover the actual exit codes and stdout a deploy pipeline would see, not
just the underlying `backend/config.py` helpers already covered by
`tests/test_config.py`.
"""

import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from tests.vectorstore.conftest import TEST_DATABASE_URL, _postgres_available

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"

requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="PostgreSQL test database not reachable (REQUIRES EXTERNAL SERVICE)",
)


def _run_script(script_name: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BACKEND_DIR / "scripts" / script_name)],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestCheckProductionConfig:
    def test_exits_zero_when_database_url_is_set(self, monkeypatch):
        env = {"PATH": "/usr/bin:/bin", "DATABASE_URL": "postgresql://user@localhost/db"}
        result = _run_script("check_production_config.py", env)

        assert result.returncode == 0
        assert "OK: all required settings are configured." in result.stdout

    def test_exits_one_and_names_the_setting_when_database_url_is_missing(self):
        env = {"PATH": "/usr/bin:/bin"}
        result = _run_script("check_production_config.py", env)

        assert result.returncode == 1
        assert "MISSING (required)" in result.stdout
        assert "DATABASE_URL" in result.stdout

    def test_never_prints_the_actual_secret_value(self):
        secret_value = "definitely-a-secret-token-98765"
        env = {
            "PATH": "/usr/bin:/bin",
            "DATABASE_URL": f"postgresql://user:{secret_value}@localhost/db",
            "GITHUB_TOKEN": secret_value,
        }
        result = _run_script("check_production_config.py", env)

        assert secret_value not in result.stdout
        assert secret_value not in result.stderr


class TestApplySchema:
    def test_exits_one_without_touching_a_database_when_database_url_is_missing(self):
        env = {"PATH": "/usr/bin:/bin"}
        result = _run_script("apply_schema.py", env)

        assert result.returncode == 1
        assert "DATABASE_URL" in result.stderr

    @requires_postgres
    def test_is_idempotent_against_a_real_database(self):
        env = {"PATH": "/usr/bin:/bin", "DATABASE_URL": TEST_DATABASE_URL}

        first = _run_script("apply_schema.py", env)
        second = _run_script("apply_schema.py", env)

        assert first.returncode == 0
        assert second.returncode == 0
        assert "Schema applied" in first.stdout
        assert "Schema applied" in second.stdout

    @requires_postgres
    def test_creates_no_destructive_statements_and_preserves_existing_rows(self):
        conn = psycopg.connect(TEST_DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO repositories (name, root_path) "
                "VALUES ('apply-schema-test-repo', '/tmp/apply-schema-test-repo-unique-marker') "
                "RETURNING id;"
            )
            repo_id = cur.fetchone()[0]
        conn.commit()

        try:
            env = {"PATH": "/usr/bin:/bin", "DATABASE_URL": TEST_DATABASE_URL}
            result = _run_script("apply_schema.py", env)
            assert result.returncode == 0

            with conn.cursor() as cur:
                cur.execute("SELECT id FROM repositories WHERE id = %s;", (repo_id,))
                assert cur.fetchone() is not None
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM repositories WHERE id = %s;", (repo_id,))
            conn.commit()
            conn.close()
