"""Exceptions raised by the repository tools subsystem."""


class ToolError(Exception):
    """Base class for all tool-related errors."""


class ToolInputError(ToolError):
    """Raised when tool input fails validation or a precondition check."""


class ToolAuthorizationError(ToolError):
    """Raised when a tool call would cross a safety boundary (e.g. a path
    that resolves outside the repository root).
    """


class ToolNotAvailableError(ToolError):
    """Raised by a tool that is intentionally not implemented rather than
    approximated — e.g. get_callers/get_callees, which would require
    call-site data Phase 2 does not extract. See tools/graph_tools.py.
    """
