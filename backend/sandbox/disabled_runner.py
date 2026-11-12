"""The test runner used when the sandbox is switched off.

`DisabledTestRunner` executes NOTHING: it is not an "unsandboxed"
alternative (which `sandbox/base.py` forbids), it is the absence of test
execution, made explicit. It raises `SandboxDisabledError` so the agent
reports "tests not run: sandbox disabled in this deployment" — instead of
relying on the API image happening to lack a Docker CLI, which would
silently turn test execution on if a Docker CLI and socket ever appeared.

`configured_test_runner()` is what the API's agent worker uses: Docker
only when SANDBOX_ENABLED is exactly "true" (backend/config.py).
"""

import config
from sandbox.base import TestRunner
from sandbox.docker_runner import DockerTestRunner
from sandbox.exceptions import SandboxDisabledError
from sandbox.models import TestRunResult


class DisabledTestRunner(TestRunner):
    def run(self, root_path: str) -> TestRunResult:
        raise SandboxDisabledError("sandbox disabled in this deployment")


def configured_test_runner() -> TestRunner:
    """Read at call time (once per agent task), never cached at import."""
    return DockerTestRunner() if config.sandbox_enabled() else DisabledTestRunner()
