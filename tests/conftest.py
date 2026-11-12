"""F8: refuse to start a test session that would run against the
application's own database.

Database fixtures in this suite are destructive inside TEST_DATABASE_URL
(e.g. tests/agent_tasks/conftest.py deletes every `agent_tasks` row). If
TEST_DATABASE_URL names the application's database, a plain `pytest`
would wipe real data. The application's database is whatever
`evaluation.safety.application_database_url()` says: DATABASE_URL, or —
when it is unset — the default the app falls back to
(`<user>@localhost:5432/ai_swe_agent`), protected exactly the same way.
"Same" is decided the way `evaluation.safety` decides it: an identical
URL is refused without connecting; otherwise by what the SERVER reports
(cluster `system_identifier` + `current_database()`), so `localhost` vs
`127.0.0.1` or extra connection parameters cannot slip through.

Not refused: an unreachable application database (nothing can be written
to it), or an unreachable TEST_DATABASE_URL (the database tests skip
themselves).

The one deliberate exception: `--with-regression` in the evaluation CLI
re-runs suites in a child pytest with BOTH variables pinned to a database
it has ALREADY verified is not the application's (see
backend/evaluation/runners/regression.py). It says so with
PYTEST_DATABASE_URL_IS_VERIFIED_DISPOSABLE=1. Nothing else should set it.
"""

import getpass
import os

import psycopg
import pytest

VERIFIED_DISPOSABLE_ENV = "PYTEST_DATABASE_URL_IS_VERIFIED_DISPOSABLE"


def _test_database_url() -> str:
    # Same default as tests/vectorstore/conftest.py.
    return os.environ.get("TEST_DATABASE_URL", f"postgresql://{getpass.getuser()}@localhost:5432/ai_swe_agent_test")


def application_database_conflict() -> str:
    """Why this session must not run, or "" when it is safe."""
    if os.environ.get(VERIFIED_DISPOSABLE_ENV) == "1":
        return ""
    from evaluation.safety import application_database_url, database_identity

    # The database the application WOULD use: DATABASE_URL, or the same
    # default the app falls back to when it is unset (M1: an unset
    # DATABASE_URL used to disable this guard entirely).
    application_url = application_database_url()
    test_url = _test_database_url()
    if test_url == application_url:
        return "TEST_DATABASE_URL is the same URL as the application database (DATABASE_URL or its default)"

    try:
        application = database_identity(application_url)
        test = database_identity(test_url)
    except psycopg.OperationalError:
        return ""  # one side unreachable: nothing to protect, or the database tests skip
    if application == test:
        return "TEST_DATABASE_URL points at the application database (compared by server identity)"
    return ""


def pytest_sessionstart(session):
    reason = application_database_conflict()
    if reason:
        pytest.exit(
            f"Refusing to run: {reason}. Database fixtures delete rows in TEST_DATABASE_URL; "
            "point it at a disposable database (default: ai_swe_agent_test).",
            returncode=2,
        )


# Settings that would make a test call a REAL hosted LLM. A developer's .env
# (exported before running pytest) may set them; a test run must never spend
# free-tier quota or credits by accident. Tests that need a provider set these
# explicitly with monkeypatch and fake the network client.
_REAL_LLM_SELECTORS = (
    "LLM_PROVIDER", "GROQ_API_KEY", "LLM_MODEL", "LLM_REASONING_EFFORT", "LLM_MAX_COMPLETION_TOKENS",
    # Embedding selection too: a .env choosing fastembed must not make the
    # whole suite load (or download) a real model.
    "EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "EMBEDDING_MODEL_PATH", "EMBEDDING_BATCH_SIZE", "EMBEDDING_THREADS",
)


@pytest.fixture(autouse=True)
def _no_accidental_real_llm(monkeypatch):
    for name in _REAL_LLM_SELECTORS:
        monkeypatch.delenv(name, raising=False)
