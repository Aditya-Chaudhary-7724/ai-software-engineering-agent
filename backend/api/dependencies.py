"""Shared FastAPI dependencies for `/api/v1`.

Each external resource is a separate dependency so tests can replace it
with `app.dependency_overrides` (a temporary trace directory, a fake
Neo4j client) without monkeypatching module internals.
"""

import os
from pathlib import Path
from typing import Callable

from fastapi import Depends, Path as PathParam, Request

from graph.client import Neo4jClient
from observability.recorder import DEFAULT_TRACE_DIR

from api import repository_catalog
from api.errors import APIError
from api.repository_catalog import RepositoryRecord

# Postgres BIGSERIAL upper bound — anything larger can't be a real id.
_MAX_REPOSITORY_ID = 2**63 - 1


def get_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise APIError(503, "database_not_configured")
    return database_url


def get_trace_directory() -> Path:
    return Path(DEFAULT_TRACE_DIR)


def get_neo4j_client_factory() -> Callable[[], Neo4jClient]:
    return Neo4jClient


def get_repository_record(
    repository_id: int = PathParam(..., ge=1, le=_MAX_REPOSITORY_ID),
    database_url: str = Depends(get_database_url),
) -> RepositoryRecord:
    """The ONLY way a route obtains a repository's root path: looked up
    server-side from a trusted id, never accepted from the client
    (docs/security.md section 15's "root_path must be pinned by the
    trusted host process").
    """
    record = repository_catalog.get_repository(database_url, repository_id)
    if record is None:
        raise APIError(404, "repository_not_found")
    return record


def require_repository_on_disk(record: RepositoryRecord = Depends(get_repository_record)) -> RepositoryRecord:
    # A repository row can outlive its files (e.g. an evaluation run's
    # temporary directory, already cleaned up).
    if not Path(record.root_path).is_dir():
        raise APIError(409, "repository_files_unavailable")
    return record


def get_workspace_root() -> str:
    """The server-side directory GitHub repositories are cloned into.
    Configured by the operator (WORKSPACE_ROOT), never by a request."""
    workspace_root = os.environ.get("WORKSPACE_ROOT", "")
    if not workspace_root or not Path(workspace_root).is_absolute():
        raise APIError(503, "ingestion_not_configured")
    return workspace_root


def get_job_worker(request: Request):
    """The in-process worker started by the API lifespan. Without a running
    worker a submitted job would sit queued forever, so submissions are
    refused instead (503)."""
    worker = getattr(request.app.state, "ingestion_worker", None)
    if worker is None or not worker.is_running:
        raise APIError(503, "job_worker_unavailable")
    return worker
