"""Repository analysis tools (Phase 8).

Formalizes the ad-hoc function calls Phase 7's agent nodes made
directly into a proper tool abstraction: declarative input/output
schemas (Pydantic), validation, structured error handling, and
explicit authorization boundaries (tools/security.py's path-traversal
guard). Read-only by construction — no write/execute capability exists
in this package; that's Phase 9/10's concern, gated by human approval.

get_callers/get_callees are intentionally NOT implemented via
name-matching approximation — see tools/graph_tools.py — because Phase
2 doesn't extract call-site data, and a plausible-looking wrong answer
is worse than an honest "not available".
"""

from tools.exceptions import ToolAuthorizationError, ToolError, ToolInputError, ToolNotAvailableError
from tools.registry import Tool, ToolRegistry, ToolResult
from tools.service import build_default_registry

__all__ = [
    "ToolError",
    "ToolInputError",
    "ToolAuthorizationError",
    "ToolNotAvailableError",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "build_default_registry",
]
