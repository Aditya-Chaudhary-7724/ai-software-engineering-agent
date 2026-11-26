"""Guards the Docker build context (.dockerignore).

Regression: .dockerignore patterns match from the context ROOT, so `.env.*`
did not exclude `frontend/.env.local` (operator secrets), and `node_modules/`
and `.next/` did not exclude the frontend's host copies. The frontend build
stage copied all three, and Next's build reads `.env.local`. This test asks
Docker itself what the context contains, when Docker is available.
"""

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

MUST_BE_EXCLUDED = [
    "frontend/.env.local",
    "frontend/.env",
    "frontend/node_modules/x/index.js",
    "frontend/.next/cache/x",
    "backend/__pycache__/x.pyc",
    ".venv311/bin/python",
    ".env",
    "deploy/prod.env",
]
MUST_BE_INCLUDED = [".env.example", "frontend/.env.example", "frontend/package.json", "backend/requirements.txt"]


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0


@pytest.mark.skipif(not _docker_available(), reason="Docker daemon not available")
def test_build_context_excludes_secrets_and_host_build_artifacts(tmp_path):
    context = tmp_path / "ctx"
    context.mkdir()
    shutil.copy(ROOT / ".dockerignore", context / ".dockerignore")
    for rel in MUST_BE_EXCLUDED + MUST_BE_INCLUDED:
        target = context / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM busybox:1.37\nCOPY . /ctx\nRUN cd /ctx && find . -type f | sort\n")
    tag = f"dockerignore-probe-{uuid.uuid4().hex[:8]}"
    result = subprocess.run(
        ["docker", "build", "--no-cache", "--progress=plain", "-t", tag, "-f", str(dockerfile), str(context)],
        capture_output=True, text=True, timeout=300,
    )
    subprocess.run(["docker", "image", "rm", "-f", tag], capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr[-2000:]
    listed = {line.split(" ./", 1)[1].strip() for line in result.stderr.splitlines() if " ./" in line}
    for rel in MUST_BE_EXCLUDED:
        assert rel not in listed, f"{rel} reached the Docker build context"
    for rel in MUST_BE_INCLUDED:
        assert rel in listed, f"{rel} should be in the build context"
