"""Shared safety boundary: every tool that takes a file path must go
through this before touching the filesystem.

Prevents path traversal (`../../etc/passwd`) escaping the repository
root a tool was scoped to — the concrete safety boundary "path
restrictions" means in practice for a tool an LLM-driven agent calls
with a string it may not have fully validated itself.
"""

from pathlib import Path

from tools.exceptions import ToolAuthorizationError


def resolve_safe_path(root_path: str, relative_path: str) -> Path:
    root = Path(root_path).resolve()
    candidate = (root / relative_path).resolve()

    try:
        candidate.relative_to(root)
    except ValueError:
        raise ToolAuthorizationError(
            f"Path '{relative_path}' resolves outside the repository root and was refused."
        )

    return candidate
