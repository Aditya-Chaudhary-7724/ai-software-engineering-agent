"""Shared fixtures for sandbox tests.

Real-Docker tests are marked `requires_docker` and skip cleanly (rather
than failing) when the Docker daemon isn't reachable, per this
project's "LOCAL TESTED vs REQUIRES EXTERNAL SERVICE" distinction —
same pattern as tests/vectorstore/conftest.py's `requires_postgres` and
tests/graph/conftest.py's `requires_neo4j`. `requires_sandbox_image`
additionally skips if the custom sandbox image hasn't been built
locally yet (see backend/sandbox/docker/python-test.Dockerfile).
"""

import subprocess

import pytest

from sandbox.models import PYTHON_SANDBOX_IMAGE


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _sandbox_image_built() -> bool:
    try:
        subprocess.run(
            ["docker", "image", "inspect", PYTHON_SANDBOX_IMAGE], capture_output=True, timeout=5, check=True
        )
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


requires_docker = pytest.mark.skipif(not _docker_available(), reason="Docker not reachable (REQUIRES EXTERNAL SERVICE)")

requires_sandbox_image = pytest.mark.skipif(
    not (_docker_available() and _sandbox_image_built()),
    reason=(
        f"'{PYTHON_SANDBOX_IMAGE}' image not built locally. Build it with: "
        "docker build -t ai-swe-agent-sandbox-python:latest "
        "-f backend/sandbox/docker/python-test.Dockerfile backend/sandbox/docker"
    ),
)
