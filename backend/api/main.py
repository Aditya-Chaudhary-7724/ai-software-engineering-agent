"""Phase 15: the production entry point.

This is deliberately a MINIMAL FastAPI application — its job is to
make this project's deployment concerns (health checks, containerized
production process, structured logging, non-secret-leaking error
handling) concretely real and testable, not to expose the full
agent/evaluation/GitHub functionality over HTTP. That is a genuine,
separate future extension (reusing the exact same `AgentService`/
`EvaluationSuite`/`GitHubIntegrationService` already built in Phases
7-12, unchanged), intentionally out of scope here — see
docs/deployment.md for why. Building that surface hastily, under a
"production deployment" phase whose actual job is infrastructure, risks
shipping a half-considered API and would go well beyond what was asked.

Frontend phase F1 adds `/api/v1` (api/v1.py): authenticated endpoints
(capabilities, repositories, files, graph summary, traces). F3 adds
background ingestion/re-index jobs and starts their in-process worker in
`lifespan`. F6 adds agent tasks with bound human approval and a second
in-process worker for them (durable approvals via PostgreSQL checkpoints).
Standalone test runs and GitHub push/PR remain unexposed.

Why FastAPI: it's built on Pydantic, already a dependency since Phase 8
(tool input/output schemas) — this introduces no new validation
paradigm, just a thin ASGI layer over one already in use. Uvicorn
(ASGI server) + Gunicorn (process manager) is the standard, documented
production pairing for it — see backend/Dockerfile.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agent_tasks.executor import AgentTaskDependencies, AgentTaskExecutor
from agent_tasks.models import DEFAULT_APPROVAL_TTL_SECONDS
from agent_tasks.store import AgentTaskStore
from jobs.ingestion import DEFAULT_MAX_REPOSITORY_KB, IngestionDependencies, IngestionExecutor
from jobs.store import JobStore
from jobs.worker import BackgroundWorker, IngestionWorker
from observability.logging_config import configure_json_logging

from api.errors import APIError, api_error_handler, validation_error_handler
from api.health import check_liveness, check_readiness
from api.v1 import API_V1_PREFIX, build_v1_router

logger = logging.getLogger("api")

# CORS is opt-in via an explicit allowlist, never a wildcard default:
# ALLOWED_ORIGINS is a comma-separated list of exact origins. Empty by
# default, and an empty allowlist means Starlette's CORSMiddleware never
# adds CORS headers. The Engineering Lab frontend does not need it: it is
# a backend-for-frontend whose SERVER calls this API with
# API_SERVICE_TOKEN, so no browser ever calls this API directly.
_ALLOWED_ORIGINS = [origin.strip() for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",") if origin.strip()]


def _build_ingestion_worker() -> Optional[IngestionWorker]:
    """F3: one in-process worker per API process, only when ingestion is
    fully configured. INGESTION_WORKER_ENABLED=false turns it off (e.g. for
    a process that should only serve reads)."""
    database_url = os.environ.get("DATABASE_URL")
    workspace_root = os.environ.get("WORKSPACE_ROOT")
    if os.environ.get("INGESTION_WORKER_ENABLED", "true").lower() == "false":
        logger.info("ingestion worker disabled by INGESTION_WORKER_ENABLED")
        return None
    if not database_url or not workspace_root or not os.path.isabs(workspace_root):
        logger.info("ingestion worker not started: DATABASE_URL and an absolute WORKSPACE_ROOT are required")
        return None
    max_kb = os.environ.get("INGESTION_MAX_REPOSITORY_KB", "")
    store = JobStore(database_url)
    deps = IngestionDependencies(
        database_url=database_url,
        workspace_root=workspace_root,
        max_repository_kb=int(max_kb) if max_kb.isdigit() else DEFAULT_MAX_REPOSITORY_KB,
    )
    return IngestionWorker(store, IngestionExecutor(store, deps))


def _build_agent_worker() -> Optional[BackgroundWorker]:
    """F6: the agent-task worker. Agent tasks take minutes (LLM calls,
    sandboxed test runs), so they never run inside an HTTP request.
    AGENT_WORKER_ENABLED=false turns it off; AGENT_APPROVAL_TTL_SECONDS
    bounds how long a proposed change may wait for a decision."""
    database_url = os.environ.get("DATABASE_URL")
    if os.environ.get("AGENT_WORKER_ENABLED", "true").lower() == "false":
        logger.info("agent worker disabled by AGENT_WORKER_ENABLED")
        return None
    if not database_url:
        logger.info("agent worker not started: DATABASE_URL is required")
        return None
    ttl = os.environ.get("AGENT_APPROVAL_TTL_SECONDS", "")
    deps = AgentTaskDependencies(
        database_url=database_url,
        approval_ttl_seconds=int(ttl) if ttl.isdigit() and int(ttl) > 0 else DEFAULT_APPROVAL_TTL_SECONDS,
    )
    store = AgentTaskStore(database_url)
    executor = AgentTaskExecutor(store, deps)
    # A task can legitimately run for minutes (sandboxed tests: up to the
    # testing loop's 300 s budget), so "stale" must be well past one
    # heartbeat interval, not past one task duration — heartbeats continue
    # for the whole run.
    return BackgroundWorker(store, executor, name="agent", recover=executor.recover_stale)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if os.environ.get("APP_ENV") == "production":
        configure_json_logging()
    logger.info("application startup", extra={"app_env": os.environ.get("APP_ENV", "development")})
    workers = {"ingestion_worker": _build_ingestion_worker(), "agent_worker": _build_agent_worker()}
    for attribute, worker in workers.items():
        setattr(app.state, attribute, worker)
        if worker is not None:
            worker.start()
    try:
        yield
    finally:
        for worker in workers.values():
            if worker is not None:
                worker.stop(timeout_seconds=10)
        logger.info("application shutdown")


def create_app() -> FastAPI:
    is_production = os.environ.get("APP_ENV") == "production"

    app = FastAPI(
        title="AI Software Engineering Agent",
        version="0.1.0",
        # Interactive API docs are a minor information-disclosure surface
        # (they enumerate every route) — disabled in production, matching
        # this project's own "don't expose more than necessary" posture.
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
        lifespan=lifespan,
        # F8: no automatic trailing-slash redirects. Pinned Starlette builds
        # their Location from the client's Host header (PYSEC-2026-161), and
        # they fire before authentication (no route has matched yet), so
        # `Host: evil.example` produced `Location: http://evil.example/...`.
        # The only caller (the frontend server) always uses exact paths.
        redirect_slashes=False,
    )

    if _ALLOWED_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_ALLOWED_ORIGINS,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            # F8: only what an /api/v1 call needs, not every header.
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.get("/")
    async def root() -> dict:
        return {"service": "ai-software-engineering-agent", "status": "ok"}

    @app.get("/health/live")
    async def health_live() -> dict:
        return check_liveness()

    @app.get("/health/ready")
    async def health_ready() -> JSONResponse:
        result = check_readiness()
        return JSONResponse(
            status_code=200 if result.ready else 503,
            content={
                "ready": result.ready,
                "dependencies": [
                    {"name": d.name, "ok": d.ok, "detail": d.detail} for d in result.dependencies
                ],
            },
        )

    # F1: authenticated, read-only /api/v1 (see api/v1.py). Health
    # endpoints above stay unauthenticated, exactly as before — an
    # orchestrator's probes carry no credentials.
    app.include_router(build_v1_router())
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    @app.middleware("http")
    async def no_store_for_api_v1(request: Request, call_next):
        response = await call_next(request)
        # API responses can carry repository source code: never let a
        # browser or intermediary cache them. F8: decided on the raw ASGI
        # path the router matches, NOT `request.url.path` — pinned
        # Starlette rebuilds that from the client's Host header
        # (PYSEC-2026-161/-248), so `Host: evil/x?` made it `/x` and
        # skipped these headers (tests/api/test_f8_header_hardening.py).
        if request.scope["path"].startswith(API_V1_PREFIX):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Production error handling: the HTTP response NEVER includes a
        # stack trace, an exception message, a file path, or anything
        # else that could carry a secret or internal detail — only a
        # fixed, generic body. The real (still-redacted, per Phase 13's
        # Tracer/logging_config) detail goes to the server-side
        # structured log only.
        logger.error("unhandled exception on %s", request.scope["path"], exc_info=True)
        return JSONResponse(status_code=500, content={"error": "internal_server_error"})

    return app


app = create_app()
