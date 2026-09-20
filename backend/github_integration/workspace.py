"""Resolves a safe local clone destination under a controlled workspace
root.

Reuses Phase 8's `tools.security.resolve_safe_path` unchanged — the
same path-traversal boundary already verified against real traversal
attempts in `tests/tools/test_security.py`, applied here to where a
repository is cloned TO rather than where a tool reads FROM. Since
`owner`/`repo` are only ever produced by `url_validation.parse_github_url`
(which already restricts them to a strict alnum/`._-` character set),
this is defense-in-depth: even if that guarantee were ever weakened,
`resolve_safe_path` independently refuses anything that would resolve
outside `workspace_root`.
"""

from pathlib import Path

from tools.exceptions import ToolAuthorizationError
from tools.security import resolve_safe_path

from github_integration.exceptions import WorkspaceError
from github_integration.models import RepositoryReference


def resolve_clone_destination(workspace_root: str, reference: RepositoryReference) -> Path:
    Path(workspace_root).mkdir(parents=True, exist_ok=True)
    try:
        return resolve_safe_path(workspace_root, f"{reference.owner}/{reference.repo}")
    except ToolAuthorizationError as exc:
        raise WorkspaceError(str(exc)) from exc
