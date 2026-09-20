"""Liveness vs readiness — a deliberate distinction (Phase 15):

- LIVENESS answers "is the application process alive and not
  deadlocked?" It checks nothing external. A temporary Neo4j or
  Postgres outage must never make this report the process as dead —
  that would cause an orchestrator (Kubernetes, ECS, ...) to needlessly
  kill and restart an otherwise-healthy process, and could even cause a
  crash-loop if the outage is prolonged.
- READINESS answers "can this instance currently serve requests that
  need a specific dependency?" Each dependency is checked
  independently, with a short timeout, and reported individually —
  never collapsed into one all-or-nothing boolean — because a Neo4j
  outage should degrade hybrid retrieval, not make every endpoint
  (including ones that only need Postgres) appear unavailable too.

Overall `ready` is driven by PostgreSQL only: every RAG/agent code path
in this project depends on it (Phase 3), matching `backend/config.py`'s
own REQUIRED/OPTIONAL classification. Neo4j is OPTIONAL — its absence
or unavailability is reported, not treated as a readiness failure.
"""

import os
from dataclasses import dataclass, field
from typing import List

import psycopg

from graph.client import Neo4jClient
from graph.exceptions import MissingCredentialsError as Neo4jMissingCredentialsError

_DEPENDENCY_CHECK_TIMEOUT_SECONDS = 2


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    dependencies: List[DependencyStatus] = field(default_factory=list)


def check_liveness() -> dict:
    return {"status": "alive"}


def _check_postgres() -> DependencyStatus:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return DependencyStatus("postgres", False, "DATABASE_URL is not configured")
    try:
        conn = psycopg.connect(database_url, connect_timeout=_DEPENDENCY_CHECK_TIMEOUT_SECONDS)
        conn.close()
        return DependencyStatus("postgres", True, "reachable")
    except psycopg.OperationalError:
        # Never include the raw exception text: some drivers can embed
        # connection parameters in an error message — report a fixed,
        # safe string instead (same "don't even risk it" posture as
        # Phase 13/14's redaction).
        return DependencyStatus("postgres", False, "unreachable")


def _check_neo4j() -> DependencyStatus:
    try:
        client = Neo4jClient()
    except Neo4jMissingCredentialsError:
        # Not configured is not a failure — Neo4j is optional.
        return DependencyStatus("neo4j", True, "not configured (optional)")
    try:
        client.verify_connectivity()
        return DependencyStatus("neo4j", True, "reachable")
    except Exception:
        return DependencyStatus("neo4j", False, "unreachable")
    finally:
        client.close()


def check_readiness() -> ReadinessResult:
    postgres_status = _check_postgres()
    neo4j_status = _check_neo4j()
    return ReadinessResult(ready=postgres_status.ok, dependencies=[postgres_status, neo4j_status])
