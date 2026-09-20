"""Plain, dependency-free data models for the sandbox subsystem.

Same rationale as ingestion/parsing's dataclasses: no API boundary
exists yet to justify a validation library here.
"""

from dataclasses import dataclass
from typing import List, Optional

# The only image this phase knows how to run tests in: a minimal
# Python 3.11 image with pytest preinstalled (see sandbox/docker/), so
# the common case needs no network access inside the container. Built
# locally via `docker build -t ai-swe-agent-sandbox-python:latest -f
# backend/sandbox/docker/python-test.Dockerfile backend/sandbox/docker`
# (see README.md). Not pushed to any registry.
PYTHON_SANDBOX_IMAGE = "ai-swe-agent-sandbox-python:latest"

DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MEMORY_LIMIT = "512m"
DEFAULT_CPU_LIMIT = 1.0
DEFAULT_PIDS_LIMIT = 128


@dataclass(frozen=True)
class SandboxLimits:
    """Resource and access limits applied to every sandboxed test run."""

    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    memory_limit: str = DEFAULT_MEMORY_LIMIT
    cpu_limit: float = DEFAULT_CPU_LIMIT
    pids_limit: int = DEFAULT_PIDS_LIMIT
    # Network is disabled by default per this project's security policy
    # ("restrict network access unless a specific test genuinely requires
    # it"). No test in this project's own scope needs it; a caller may
    # opt out for one that genuinely does.
    network_disabled: bool = True


@dataclass(frozen=True)
class TestRunResult:
    """The outcome of one sandboxed test-suite execution."""

    command: List[str]
    exit_code: Optional[int]  # None only when timed_out is True
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float

    @property
    def passed(self) -> bool:
        return (not self.timed_out) and self.exit_code == 0
