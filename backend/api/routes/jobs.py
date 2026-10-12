"""Repository ingestion / re-index jobs (frontend phase F3).

The ONLY write operations under /api/v1. Both endpoints only ENQUEUE work
for the in-process worker (jobs/worker.py) and return immediately; the
client polls `GET /ingestion-jobs/{id}` until a terminal status.

Trust boundaries:
- the client sends a GitHub HTTPS URL (ingest) or a repository id
  (re-index) — never a filesystem path. Request bodies forbid unknown
  fields, so even a `local_path` key is rejected outright;
- the checkout path is computed server-side from the validated URL
  (`resolve_clone_destination`) or looked up from the trusted id;
- duplicate/concurrent submissions are resolved by the database's
  one-active-job-per-target index, returning the existing job.
"""

import os
import uuid
from typing import Callable, Optional

import psycopg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from github_integration.exceptions import InvalidRepositoryURLError, WorkspaceError
from github_integration.workspace import resolve_clone_destination
from graph.client import Neo4jClient
from graph.exceptions import MissingCredentialsError as Neo4jMissingCredentialsError
from jobs import models as job_models
from jobs.ingestion import canonical_github_reference, find_repository_id, github_full_name_for
from jobs.store import JobStore

from api.dependencies import (
    get_database_url,
    get_job_worker,
    get_neo4j_client_factory,
    get_workspace_root,
    require_repository_on_disk,
)
from api.errors import APIError
from api.repository_catalog import RepositoryRecord
from api.schemas import Job, JobList, JobSubmission

router = APIRouter(tags=["jobs"])

# Only these progress keys are ever returned (all set by jobs/ingestion.py
# from real results); anything else stored on a job stays server-side.
_PUBLIC_PROGRESS_KEYS = {"chunks_indexed", "embedding_provider", "default_branch", "graph_error_code", "graph_node_counts"}


class IngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    github_url: str = Field(..., min_length=1, max_length=300)
    build_graph: StrictBool = False


class ReindexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    build_graph: StrictBool = False


def _to_api(job: job_models.Job) -> Job:
    return Job(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        stage=job.stage,
        github_full_name=job.github_full_name,
        repository_id=job.repository_id,
        build_graph=job.build_graph,
        graph_status=job.graph_status,
        progress={k: v for k, v in job.progress.items() if k in _PUBLIC_PROGRESS_KEYS},
        error_code=job.error_code,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _with_db_errors(operation):
    try:
        return operation()
    except psycopg.errors.UndefinedTable:
        raise APIError(503, "schema_not_initialized")
    except psycopg.OperationalError:
        raise APIError(503, "database_unavailable")


def _require_graph_configured(build_graph: bool, client_factory: Callable[[], Neo4jClient]) -> None:
    if not build_graph:
        return
    try:
        client_factory().close()
    except Neo4jMissingCredentialsError:
        raise APIError(409, "graph_not_configured")


def _submission(job: job_models.Job, created: bool, worker) -> JSONResponse:
    if created:
        worker.notify()
    body = JobSubmission(job=_to_api(job), created=created)
    return JSONResponse(status_code=202 if created else 200, content=body.model_dump(mode="json"))


@router.post("/ingestion-jobs", response_model=JobSubmission, status_code=202)
def submit_github_ingestion(
    request: IngestionRequest,
    database_url: str = Depends(get_database_url),
    workspace_root: str = Depends(get_workspace_root),
    worker=Depends(get_job_worker),
    neo4j_client_factory: Callable[[], Neo4jClient] = Depends(get_neo4j_client_factory),
):
    try:
        reference = canonical_github_reference(request.github_url)
        destination = str(resolve_clone_destination(workspace_root, reference))
    except (InvalidRepositoryURLError, WorkspaceError):
        raise APIError(422, "invalid_github_url")
    _require_graph_configured(request.build_graph, neo4j_client_factory)

    existing_id = _with_db_errors(lambda: find_repository_id(database_url, destination))
    if existing_id is not None:
        raise APIError(409, "repository_already_ingested", details={"repository_id": existing_id})

    job, created = _with_db_errors(
        lambda: JobStore(database_url).create_or_get_active(
            job_type=job_models.JOB_TYPE_INGEST_GITHUB,
            target_path=destination,
            github_full_name=f"{reference.owner}/{reference.repo}",
            repository_id=None,
            build_graph=request.build_graph,
        )
    )
    return _submission(job, created, worker)


@router.post("/repositories/{repository_id}/reindex", response_model=JobSubmission, status_code=202)
def submit_reindex(
    request: ReindexRequest,
    record: RepositoryRecord = Depends(require_repository_on_disk),
    database_url: str = Depends(get_database_url),
    worker=Depends(get_job_worker),
    neo4j_client_factory: Callable[[], Neo4jClient] = Depends(get_neo4j_client_factory),
):
    _require_graph_configured(request.build_graph, neo4j_client_factory)
    job, created = _with_db_errors(
        lambda: JobStore(database_url).create_or_get_active(
            job_type=job_models.JOB_TYPE_REINDEX,
            target_path=record.root_path,
            github_full_name=github_full_name_for(record.root_path, os.environ.get("WORKSPACE_ROOT")),
            repository_id=record.id,
            build_graph=request.build_graph,
        )
    )
    return _submission(job, created, worker)


@router.get("/ingestion-jobs", response_model=JobList)
def list_jobs(
    repository_id: Optional[int] = Query(None, ge=1),
    active: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
    database_url: str = Depends(get_database_url),
) -> JobList:
    jobs = _with_db_errors(lambda: JobStore(database_url).list_recent(limit, repository_id, active))
    return JobList(jobs=[_to_api(j) for j in jobs])


@router.get("/ingestion-jobs/{job_id}", response_model=Job)
def get_job(job_id: str, database_url: str = Depends(get_database_url)) -> Job:
    try:
        canonical = str(uuid.UUID(job_id))
    except (ValueError, AttributeError):
        raise APIError(400, "invalid_job_id")
    if canonical != job_id:
        raise APIError(400, "invalid_job_id")
    job = _with_db_errors(lambda: JobStore(database_url).get(canonical))
    if job is None:
        raise APIError(404, "job_not_found")
    return _to_api(job)
