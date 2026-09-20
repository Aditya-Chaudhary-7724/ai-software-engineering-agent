# Phase 15: production image for the BACKEND API ONLY.
#
# This is NOT the Phase 10 sandbox image
# (backend/sandbox/docker/python-test.Dockerfile). That image executes
# UNTRUSTED repository/test code and must remain completely separate
# from this one, which runs THIS project's OWN trusted application
# code and holds real credentials (DATABASE_URL, LLM_API_KEY,
# GITHUB_TOKEN, ...). This image has no Docker CLI, no Docker socket,
# and no ability to execute repository code at all — conflating the
# two would mean the process holding production secrets could also
# execute arbitrary untrusted code, exactly the escalation Phase 10's
# isolation exists to prevent. See docs/deployment.md's "Sandbox
# Deployment" section for the full boundary.
#
# Build (from the repository root, so the build context includes both
# backend/requirements.txt and backend/ — see .dockerignore for what's
# excluded from that context):
#   docker build -t ai-swe-agent-backend:latest -f backend/Dockerfile .
#
# Run:
#   docker run --rm -p 8000:8000 --env-file .env ai-swe-agent-backend:latest

FROM python:3.11-slim

# Deterministic dependency installation: no pip cache, pinned versions
# from requirements.txt (already pinned per this project's own
# dependency policy — see that file's own comments).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/

# Non-root user: this container holds real production credentials via
# environment variables (never baked into the image — see below) and
# must not run as root.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

WORKDIR /app/backend

# No secrets are ever COPY'd into this image: only backend/requirements.txt
# and backend/ source are copied above — .env lives at the repository
# root and is never referenced by any COPY instruction, and is also
# excluded from the build context entirely by ../.dockerignore as a
# second, independent layer of protection. Real secrets are injected at
# container RUN time via environment variables / the deployment
# platform's secret manager — see docs/deployment.md's "Secrets" section.

EXPOSE 8000

# Liveness-only healthcheck (matches api/health.py's own liveness/
# readiness distinction): a container orchestrator's own readiness
# probe should hit /health/ready separately, with its own retry/backoff
# policy appropriate to that platform — this HEALTHCHECK exists for
# `docker run` standalone usage and for `docker ps`'s STATUS column.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)" || exit 1

# Gunicorn (process manager, handles worker respawn/graceful SIGTERM
# shutdown) with Uvicorn's ASGI worker class — the standard, documented
# production pairing for a FastAPI application. No --reload (that's a
# development-only flag); --workers is intentionally small and
# overridable via the WEB_CONCURRENCY env var Gunicorn itself reads.
CMD ["gunicorn", "api.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
