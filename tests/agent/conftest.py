"""Re-exports fixtures from tests/vectorstore and tests/graph — the
agent genuinely needs both a real Postgres and a real Neo4j.

Also defines FakeTestRunner, a test double for `sandbox.base.TestRunner`
used only here (never in backend/ — see that module's docstring for why
a fake/unsandboxed implementation must not live in production code).
It lets the Phase 10 fix-loop routing logic (agent/nodes.py's
run_tests_after_apply_node) be tested deterministically, with a
pre-programmed sequence of results, without ever touching Docker.
"""

from typing import List

import pytest

from sandbox.base import TestRunner
from sandbox.models import TestRunResult

from tests.graph.conftest import neo4j_client, requires_neo4j
from tests.vectorstore.conftest import requires_postgres, vector_store

__all__ = [
    "requires_postgres",
    "vector_store",
    "requires_neo4j",
    "neo4j_client",
    "FakeTestRunner",
    "make_test_result",
    "no_op_test_runner",
]


def make_test_result(passed: bool, stdout: str = "", stderr: str = "") -> TestRunResult:
    return TestRunResult(
        command=["python", "-m", "pytest", "-q"],
        exit_code=0 if passed else 1,
        stdout=stdout or ("1 passed" if passed else "1 failed"),
        stderr=stderr,
        timed_out=False,
        duration_seconds=0.05,
    )


class FakeTestRunner(TestRunner):
    def __init__(self, results: List[TestRunResult]) -> None:
        self._results = list(results)
        self.call_count = 0

    def run(self, root_path: str) -> TestRunResult:
        self.call_count += 1
        if not self._results:
            raise AssertionError("FakeTestRunner.run() called more times than results were programmed")
        return self._results.pop(0)


@pytest.fixture
def no_op_test_runner():
    """A real DockerTestRunner is safe to use directly in tests whose
    tmp_path repositories contain no test files: detect_test_command
    returns None and NoTestCommandError is raised before Docker is ever
    invoked (see sandbox/docker_runner.py). Used by tests that exercise
    the agent's answer/modify paths and don't care about test execution.
    """
    from sandbox.docker_runner import DockerTestRunner

    return DockerTestRunner()
