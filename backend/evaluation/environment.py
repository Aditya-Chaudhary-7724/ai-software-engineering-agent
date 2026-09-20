"""Small, self-contained external-service availability checks.

Mirrors the `requires_postgres`/`requires_neo4j`/`requires_docker`
skip-if-unreachable pattern used throughout this project's pytest
suite (tests/vectorstore/conftest.py, tests/graph/conftest.py,
tests/sandbox/conftest.py), but as plain functions usable outside
pytest — the evaluation CLI (`backend/scripts/run_evaluation.py`) needs
the same "skip this case honestly, don't fail it" behavior when a
service isn't reachable, and production code must not import from
tests/.
"""

import subprocess

import psycopg

from graph.client import Neo4jClient
from graph.exceptions import MissingCredentialsError as Neo4jMissingCredentialsError


def postgres_available(database_url: str) -> bool:
    try:
        conn = psycopg.connect(database_url, connect_timeout=2)
        conn.close()
        return True
    except psycopg.OperationalError:
        return False


def neo4j_available() -> bool:
    try:
        client = Neo4jClient()
    except Neo4jMissingCredentialsError:
        return False
    try:
        client.verify_connectivity()
        return True
    except Exception:
        return False
    finally:
        client.close()


def docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
