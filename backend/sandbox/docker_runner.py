"""Docker-based sandbox test runner.

Repository code must never execute directly on this host (project
policy). This shells out to the `docker` CLI via `subprocess` rather
than adding the `docker` Python SDK as a dependency — consistent with
this project's existing preference for the official low-level client
over an extra abstraction layer (raw SQL via `psycopg`, raw Cypher via
the `neo4j` driver, no ORM/query builder anywhere). Docker Desktop is
already part of this project's local environment (Neo4j itself runs in
a container — see docs/architecture.md), so this introduces no new
Python dependency.

Isolation model for every run:
  - The target repository is copied into a fresh temporary directory
    first, and that COPY — never the real repository — is bind-mounted
    into the container. A misbehaving test can never write back into
    the actual repository being analyzed.
  - `--network none` by default (`SandboxLimits.network_disabled`):
    no network access unless a caller opts in for a run that genuinely
    needs it.
  - `--read-only` root filesystem plus a small writable `/tmp` tmpfs
    and the writable workspace mount — nothing else on the container's
    filesystem can be modified.
  - `--cap-drop ALL --security-opt no-new-privileges`: no elevated
    capabilities.
  - `--memory`, `--cpus`, `--pids-limit` bound resource usage so one
    test run cannot exhaust host resources.
  - No `-e`/`--env-file` flag is ever passed, so the container never
    sees this host's environment variables or `.env` secrets — it only
    gets whatever the image itself defines.
  - A hard subprocess timeout enforces `SandboxLimits.timeout_seconds`.
    On timeout the container is force-killed (`docker kill`) as a
    best-effort cleanup, since `--rm` alone does not stop a container
    that is still running.
"""

import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, Optional

from sandbox.base import TestRunner
from sandbox.command_detection import detect_test_command
from sandbox.exceptions import DockerUnavailableError, NoTestCommandError
from sandbox.models import PYTHON_SANDBOX_IMAGE, SandboxLimits, TestRunResult

_DOCKER_AVAILABILITY_TIMEOUT_SECONDS = 5
_KILL_TIMEOUT_SECONDS = 10


def _as_text(value: Optional[object]) -> str:
    # subprocess.TimeoutExpired.stdout/stderr are typed Optional[Union[str,
    # bytes]] regardless of the `text=True` we pass to subprocess.run — at
    # runtime they are always `str` here, but this keeps mypy honest.
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


class DockerTestRunner(TestRunner):
    def __init__(self, limits: Optional[SandboxLimits] = None) -> None:
        self._limits = limits or SandboxLimits()

    def run(self, root_path: str) -> TestRunResult:
        command = detect_test_command(root_path)
        if command is None:
            raise NoTestCommandError(f"No supported test command could be detected for '{root_path}'.")
        return self._run_in_container(root_path, command, PYTHON_SANDBOX_IMAGE)

    def _run_in_container(self, root_path: str, command: List[str], image: str) -> TestRunResult:
        self._check_docker_available()

        with tempfile.TemporaryDirectory(prefix="sandbox-workspace-") as workspace_parent:
            workspace = Path(workspace_parent) / "repo"
            shutil.copytree(root_path, workspace, ignore=shutil.ignore_patterns(".git"))

            container_name = f"ai-swe-agent-sandbox-{uuid.uuid4().hex[:12]}"
            docker_cmd = self._build_docker_command(workspace, container_name, command, image)

            start = time.monotonic()
            try:
                completed = subprocess.run(
                    docker_cmd, capture_output=True, text=True, timeout=self._limits.timeout_seconds
                )
                return TestRunResult(
                    command=command,
                    exit_code=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    timed_out=False,
                    duration_seconds=time.monotonic() - start,
                )
            except subprocess.TimeoutExpired as exc:
                duration = time.monotonic() - start
                self._kill_container(container_name)
                return TestRunResult(
                    command=command,
                    exit_code=None,
                    stdout=_as_text(exc.stdout),
                    stderr=_as_text(exc.stderr),
                    timed_out=True,
                    duration_seconds=duration,
                )

    def _build_docker_command(
        self, workspace: Path, container_name: str, command: List[str], image: str
    ) -> List[str]:
        return [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none" if self._limits.network_disabled else "bridge",
            "--memory",
            self._limits.memory_limit,
            "--cpus",
            str(self._limits.cpu_limit),
            "--pids-limit",
            str(self._limits.pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=64m",
            "-w",
            "/workspace",
            "-v",
            f"{workspace}:/workspace:rw",
            image,
            *command,
        ]

    def _check_docker_available(self) -> None:
        try:
            subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=_DOCKER_AVAILABILITY_TIMEOUT_SECONDS,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise DockerUnavailableError(
                "Docker is not available. Ensure Docker Desktop is installed and running."
            ) from exc

    @staticmethod
    def _kill_container(container_name: str) -> None:
        # Best-effort: --rm won't clean up a container that timed out while
        # still running, so force-kill it directly. Failure to kill (e.g.
        # it already exited) is not itself an error worth surfacing.
        subprocess.run(
            ["docker", "kill", container_name], capture_output=True, timeout=_KILL_TIMEOUT_SECONDS, check=False
        )
