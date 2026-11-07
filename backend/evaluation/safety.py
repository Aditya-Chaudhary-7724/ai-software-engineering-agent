"""F7 guards that keep an evaluation run away from real data.

1. Files: evaluation runners only ever approve/apply changes inside a
   disposable temporary directory. With `StubLLMProvider` an approved
   "change" overwrites a file with placeholder text. That is fine in a
   throwaway sample repository and never acceptable in a real project
   checkout (e.g. a repository cloned into WORKSPACE_ROOT). Every runner
   that approves a change calls `require_disposable_root` first, so a
   future edit that points a runner at a real repository fails loudly
   instead of damaging it.

2. Databases: the agent-lifecycle runner (claims queued tasks table-wide)
   and the regression runner (re-runs pytest suites whose fixtures delete
   every `agent_tasks` row) must never run against the application's own
   database. Comparing URL strings is not enough — `localhost` and
   `127.0.0.1`, an explicit default port, or extra query parameters all
   name the same database with a different string — so
   `require_distinct_database` compares what the SERVER reports: the
   cluster's `system_identifier` plus `current_database()`.
"""

import getpass
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import psycopg


class UnsafeEvaluationTargetError(Exception):
    pass


def require_disposable_root(root: str) -> None:
    resolved = Path(root).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if temp_root not in resolved.parents:
        raise UnsafeEvaluationTargetError(f"evaluation may only modify repositories under {temp_root}")
    workspace = os.environ.get("WORKSPACE_ROOT")
    if workspace and (Path(workspace).resolve() == resolved or Path(workspace).resolve() in resolved.parents):
        raise UnsafeEvaluationTargetError("evaluation must never modify a checkout inside WORKSPACE_ROOT")


DatabaseIdentity = Tuple[str, str]

# The application's default when DATABASE_URL is unset (same as the suite
# has always used): protected exactly like an explicit DATABASE_URL.
DEFAULT_DATABASE_URL = f"postgresql://{getpass.getuser()}@localhost:5432/ai_swe_agent"


def application_database_url() -> str:
    return os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL


def database_identity(database_url: str) -> DatabaseIdentity:
    """(cluster identity, database name) as reported by the server.

    `pg_control_system()` gives the cluster's unique `system_identifier`.
    If a role may not call it, fall back to the server's address and port
    — weaker (two clusters behind one proxy would look alike) but still
    independent of how the URL was spelled. Raises psycopg.OperationalError
    when the database is unreachable."""
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        database = conn.execute("SELECT current_database()").fetchone()
        try:
            cluster = conn.execute("SELECT system_identifier::text FROM pg_control_system()").fetchone()
        except psycopg.Error:
            conn.rollback()
            cluster = conn.execute("SELECT coalesce(host(inet_server_addr()), 'local-socket') || ':' || inet_server_port()").fetchone()
    return (str(cluster[0]) if cluster else "?", str(database[0]) if database else "?")


def require_distinct_database(candidate_url: str, protected_url: Optional[str], *, purpose: str) -> None:
    """Refuse `candidate_url` if it is the same database as `protected_url`
    (normally the API's DATABASE_URL). An unreachable protected database
    cannot be told apart, so it is refused too: fail closed."""
    if not protected_url:
        return
    if candidate_url == protected_url:
        raise UnsafeEvaluationTargetError(f"{purpose}: the database must differ from DATABASE_URL (the application's database)")
    try:
        protected = database_identity(protected_url)
    except psycopg.OperationalError as exc:
        raise UnsafeEvaluationTargetError(
            f"{purpose}: DATABASE_URL is unreachable, so it cannot be verified to be a different database"
        ) from exc
    if database_identity(candidate_url) == protected:
        raise UnsafeEvaluationTargetError(
            f"{purpose}: this URL points at the same database as DATABASE_URL (the application's database)"
        )
