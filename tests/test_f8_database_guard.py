"""F8: the session guard in tests/conftest.py (pytest refuses to run its
destructive database fixtures against the application's database)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import conftest as guard
from tests.vectorstore.conftest import TEST_DATABASE_URL, requires_postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
UNREACHABLE = "postgresql://nobody@127.0.0.1:1/nowhere?connect_timeout=1"


def _respelled(url: str) -> str:
    import psycopg

    info = psycopg.conninfo.conninfo_to_dict(url)
    info["host"] = "127.0.0.1" if info.get("host") in (None, "", "localhost") else info["host"]
    info["application_name"] = "respelled"
    return psycopg.conninfo.make_conninfo(**{k: str(v) for k, v in info.items() if v is not None})


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv(guard.VERIFIED_DISPOSABLE_ENV, raising=False)
    return monkeypatch


def test_identical_urls_are_refused_without_connecting(env):
    env.setenv("DATABASE_URL", UNREACHABLE)
    env.setenv("TEST_DATABASE_URL", UNREACHABLE)
    assert "same URL" in guard.application_database_conflict()


@requires_postgres
def test_the_same_database_spelled_differently_is_refused(env):
    env.setenv("TEST_DATABASE_URL", TEST_DATABASE_URL)
    env.setenv("DATABASE_URL", _respelled(TEST_DATABASE_URL))
    assert "server identity" in guard.application_database_conflict()


@requires_postgres
def test_a_different_database_is_accepted(env):
    env.setenv("TEST_DATABASE_URL", TEST_DATABASE_URL)
    env.setenv("DATABASE_URL", TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres")
    assert guard.application_database_conflict() == ""


def test_an_unreachable_application_database_is_not_refused(env):
    env.setenv("DATABASE_URL", UNREACHABLE)
    assert guard.application_database_conflict() == ""


# -- M1: DATABASE_URL unset -------------------------------------------------------
#
# The application does not need DATABASE_URL exported: unset, it uses
# evaluation.safety.DEFAULT_DATABASE_URL (`<user>@localhost:5432/ai_swe_agent`).
# The guard used to treat "unset" as "nothing to protect", so
# `TEST_DATABASE_URL=<that default> pytest` without `.env` exported ran the
# destructive fixtures against the real application database.


def test_unset_database_url_still_protects_the_default_application_database(env):
    from evaluation import safety

    env.delenv("DATABASE_URL", raising=False)
    env.setenv("TEST_DATABASE_URL", safety.DEFAULT_DATABASE_URL)
    assert "same URL" in guard.application_database_conflict()  # decided without connecting


@requires_postgres
def test_unset_database_url_compares_the_default_by_server_identity(env):
    """A respelled default is caught too. DEFAULT_DATABASE_URL is pointed at
    the disposable test database so this never connects to real data."""
    from evaluation import safety

    env.delenv("DATABASE_URL", raising=False)
    env.setattr(safety, "DEFAULT_DATABASE_URL", TEST_DATABASE_URL)
    env.setenv("TEST_DATABASE_URL", _respelled(TEST_DATABASE_URL))
    assert "server identity" in guard.application_database_conflict()


@requires_postgres
def test_unset_database_url_with_the_usual_test_database_is_accepted(env):
    from evaluation import safety

    env.delenv("DATABASE_URL", raising=False)
    env.setattr(safety, "DEFAULT_DATABASE_URL", TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres")
    env.setenv("TEST_DATABASE_URL", TEST_DATABASE_URL)
    assert guard.application_database_conflict() == ""


def test_unset_database_url_with_an_unreachable_default_is_not_refused(env):
    from evaluation import safety

    env.delenv("DATABASE_URL", raising=False)
    env.setattr(safety, "DEFAULT_DATABASE_URL", UNREACHABLE)
    assert guard.application_database_conflict() == ""


def test_a_real_pytest_session_without_database_url_refuses_the_default_application_database():
    """End to end, the exact M1 failure: no DATABASE_URL in the environment,
    TEST_DATABASE_URL set to the application's default. The selected test
    never touches a database, so even a regressed guard could not harm
    data; the assertion is that the session refuses before running it."""
    from evaluation import safety

    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", guard.VERIFIED_DISPOSABLE_ENV)}
    env["TEST_DATABASE_URL"] = safety.DEFAULT_DATABASE_URL
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_f8_database_guard.py::test_identical_urls_are_refused_without_connecting"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Refusing to run" in result.stdout + result.stderr
    assert " passed" not in result.stdout


def test_the_verified_disposable_marker_is_the_only_override(env):
    env.setenv("DATABASE_URL", UNREACHABLE)
    env.setenv("TEST_DATABASE_URL", UNREACHABLE)
    env.setenv(guard.VERIFIED_DISPOSABLE_ENV, "1")
    assert guard.application_database_conflict() == ""
    env.setenv(guard.VERIFIED_DISPOSABLE_ENV, "true")  # only the exact value counts
    assert guard.application_database_conflict() != ""


@requires_postgres
def test_a_real_pytest_session_exits_2_before_any_test_runs():
    """End to end: a fresh pytest against a respelled TEST_DATABASE_URL
    refuses at session start; the destructive agent-task fixtures never run."""
    env = {**os.environ, "TEST_DATABASE_URL": TEST_DATABASE_URL, "DATABASE_URL": _respelled(TEST_DATABASE_URL)}
    env.pop(guard.VERIFIED_DISPOSABLE_ENV, None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/agent_tasks/test_store.py"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "Refusing to run" in result.stdout + result.stderr
    assert " passed" not in result.stdout
