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

Why FastAPI: it's built on Pydantic, already a dependency since Phase 8
(tool input/output schemas) — this introduces no new validation
paradigm, just a thin ASGI layer over one already in use. Uvicorn
(ASGI server) + Gunicorn (process manager) is the standard, documented
production pairing for it — see backend/Dockerfile.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from observability.logging_config import configure_json_logging

from api.health import check_liveness, check_readiness

logger = logging.getLogger("api")

# CORS is opt-in via an explicit allowlist, never a wildcard default:
# ALLOWED_ORIGINS is a comma-separated list of exact origins (e.g. a
# future frontend's deployed URL). Empty by default — no frontend
# exists yet (see docs/deployment.md's "Frontend" section), and an
# empty allowlist means Starlette's CORSMiddleware simply never adds
# CORS headers, which is the safest possible default for an API with
# no known legitimate browser caller yet.
_ALLOWED_ORIGINS = [origin.strip() for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if os.environ.get("APP_ENV") == "production":
        configure_json_logging()
    logger.info("application startup", extra={"app_env": os.environ.get("APP_ENV", "development")})
    yield
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
    )

    if _ALLOWED_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_ALLOWED_ORIGINS,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
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

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Production error handling: the HTTP response NEVER includes a
        # stack trace, an exception message, a file path, or anything
        # else that could carry a secret or internal detail — only a
        # fixed, generic body. The real (still-redacted, per Phase 13's
        # Tracer/logging_config) detail goes to the server-side
        # structured log only.
        logger.error("unhandled exception on %s", request.url.path, exc_info=True)
        return JSONResponse(status_code=500, content={"error": "internal_server_error"})

    return app


app = create_app()
