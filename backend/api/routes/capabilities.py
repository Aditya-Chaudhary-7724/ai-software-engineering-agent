"""What this deployment can actually do right now.

Lets the UI show honest states ("Stub LLM — answers are placeholders",
"Sandbox unavailable") instead of guessing. Reports booleans and the
fixed, safe dependency-status strings from `api.health` only — never a
configuration value.
"""

import os
import subprocess

from fastapi import APIRouter, Request

import config
from api.health import check_readiness
from api.schemas import Capabilities, DependencyCapability
from evaluation.environment import docker_available
from sandbox.models import PYTHON_SANDBOX_IMAGE

router = APIRouter(tags=["capabilities"])

# Operations exposed by THIS API version. Standalone test runs and
# evaluation runs arrive in later phases; GitHub push/PR is excluded
# entirely. `repository_ingestion`, `agent_execution` and `approvals` are
# computed per request: true only while this process's worker for them runs.
FEATURES = {
    "repository_browsing": True,
    "file_browsing": True,
    "graph_summary": True,
    "trace_viewing": True,
    "ask": True,
    "test_execution": False,
    "evaluation_runs": False,
    "github_push_and_pull_requests": False,
}


def _sandbox_image_available() -> bool:
    try:
        subprocess.run(
            ["docker", "image", "inspect", PYTHON_SANDBOX_IMAGE], capture_output=True, timeout=5, check=True
        )
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _worker_running(request: Request, attribute: str) -> bool:
    worker = getattr(request.app.state, attribute, None)
    return bool(worker is not None and worker.is_running)


@router.get("/capabilities", response_model=Capabilities)
def get_capabilities(request: Request) -> Capabilities:
    readiness = {d.name: d for d in check_readiness().dependencies}
    sandbox_on = config.sandbox_enabled()
    # Disabled means disabled: don't shell out to Docker at all.
    docker_ok = sandbox_on and docker_available()
    return Capabilities(
        api_version="v1",
        database=DependencyCapability(ok=readiness["postgres"].ok, detail=readiness["postgres"].detail),
        graph=DependencyCapability(ok=readiness["neo4j"].ok, detail=readiness["neo4j"].detail),
        llm_provider_configured=bool(os.environ.get("LLM_API_KEY")),
        embedding_provider_configured=bool(os.environ.get("EMBEDDING_API_KEY")),
        github_token_configured=bool(os.environ.get("GITHUB_TOKEN")),
        sandbox_enabled=sandbox_on,
        sandbox_docker_available=docker_ok,
        sandbox_image_available=docker_ok and _sandbox_image_available(),
        features={
            **FEATURES,
            "repository_ingestion": _worker_running(request, "ingestion_worker"),
            "agent_execution": _worker_running(request, "agent_worker"),
            "approvals": _worker_running(request, "agent_worker"),
        },
    )
