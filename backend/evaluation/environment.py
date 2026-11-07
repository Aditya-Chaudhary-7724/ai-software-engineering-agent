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


def sandbox_image_available() -> bool:
    from sandbox.models import PYTHON_SANDBOX_IMAGE

    try:
        subprocess.run(["docker", "image", "inspect", PYTHON_SANDBOX_IMAGE], capture_output=True, timeout=5, check=True)
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def database_label(database_url: str) -> str:
    """`host/dbname` only — never the user, password, or query string."""
    try:
        info = psycopg.conninfo.conninfo_to_dict(database_url)
    except psycopg.ProgrammingError:
        return "unparseable"
    return f"{info.get('host') or 'local-socket'}/{info.get('dbname') or '?'}"


def describe_environment(
    database_url: str, *, real_llm_requested: bool, real_embeddings_requested: bool, isolated_database_url: str = ""
) -> dict:
    """F7: what an evaluation run actually ran against, for the report
    header. Provider entries say what was USED: a real provider only when
    it was both requested and configured."""
    import os

    real_llm = real_llm_requested and bool(os.environ.get("LLM_API_KEY"))
    real_embeddings = real_embeddings_requested and bool(os.environ.get("EMBEDDING_API_KEY"))
    docker_ok = docker_available()
    return {
        "database": database_label(database_url) if postgres_available(database_url) else "unreachable",
        "isolated_database": (
            database_label(isolated_database_url) if isolated_database_url else "not provided"
        ),
        "neo4j": "reachable" if neo4j_available() else "unreachable",
        "docker": "reachable" if docker_ok else "unreachable",
        "sandbox_image": "present" if docker_ok and sandbox_image_available() else "absent",
        "llm_provider": "anthropic (real)" if real_llm else "StubLLMProvider (placeholder, deterministic)",
        "embedding_provider": "openai (real)" if real_embeddings else "DeterministicLocalEmbeddingProvider (hash, non-semantic)",
    }
