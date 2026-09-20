"""Exceptions raised by the sandbox test-execution subsystem."""


class SandboxError(Exception):
    """Base class for all sandbox-related errors."""


class DockerUnavailableError(SandboxError):
    """Raised when the Docker CLI/daemon is not reachable.

    Repository code must never fall back to running directly on the host
    when this is raised — callers report the failure honestly instead.
    """


class NoTestCommandError(SandboxError):
    """Raised when no supported test command could be detected for a
    repository. Only Python repositories using pytest are supported in
    this phase (see sandbox/command_detection.py) — this is an honest
    gap, not an approximation.
    """
