"""Test runner abstraction.

Deliberately has exactly one real implementation
(`docker_runner.DockerTestRunner`) — unlike the embedding/LLM provider
abstractions, this project must never ship an "unsandboxed" alternative
implementation: "repository code must never execute directly on the
host" is a hard security invariant, not a swappable backend choice.
Test doubles for this interface live in the test suite only, never in
backend/ (see tests/agent/conftest.py's FakeTestRunner). The one other
implementation in backend/, `disabled_runner.DisabledTestRunner`, runs
nothing at all: it only raises `SandboxDisabledError`.
"""

from abc import ABC, abstractmethod

from sandbox.models import TestRunResult


class TestRunner(ABC):
    @abstractmethod
    def run(self, root_path: str) -> TestRunResult:
        """Detect and run the repository's test suite in an isolated sandbox.

        Raises `sandbox.exceptions.NoTestCommandError` if no supported
        test command can be detected, and
        `sandbox.exceptions.DockerUnavailableError` if the sandbox itself
        cannot be reached, and `sandbox.exceptions.SandboxDisabledError`
        if this deployment switched it off — never falls back to running
        anything directly on the host.
        """
