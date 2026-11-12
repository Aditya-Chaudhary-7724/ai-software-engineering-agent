"""Deny-by-default scope for the public demo principal.

A request authenticated with DEMO_API_SERVICE_TOKEN may reach ONLY the
routes listed in DEMO_ALLOWED_ROUTES, and only for repositories listed in
DEMO_REPOSITORY_IDS. Everything else is refused here, in the backend, so a
bug in the frontend server cannot widen the demo's reach:
- a route not on the list -> 403 demo_forbidden (ingestion, re-index,
  approval decisions, traces, the task list, ...);
- a repository not on the list -> 404 repository_not_found, the same answer
  as for a repository that does not exist, so the demo cannot discover the
  operator's other repositories.

Attached once, at the /api/v1 router (api/v1.py), after authentication.
tests/api/test_demo_scope.py enumerates the application's real routes, so a
new route is denied to the demo until it is deliberately added here.
"""

from typing import FrozenSet, Optional, Tuple

from fastapi import Depends, Request

import config
from api.auth import Principal, require_service_token
from api.errors import APIError

_V1 = "/api/v1"

# (HTTP method, route template). Read-only routes plus Ask. Demo agent
# tasks are NOT here yet: they will be added only together with the
# propose-only mode that guarantees a demo task can never write.
DEMO_ALLOWED_ROUTES: FrozenSet[Tuple[str, str]] = frozenset(
    {
        ("GET", f"{_V1}/capabilities"),
        ("GET", f"{_V1}/repositories"),
        ("GET", f"{_V1}/repositories/{{repository_id}}"),
        ("GET", f"{_V1}/repositories/{{repository_id}}/files"),
        ("GET", f"{_V1}/repositories/{{repository_id}}/files/content"),
        ("GET", f"{_V1}/repositories/{{repository_id}}/graph/summary"),
        ("GET", f"{_V1}/repositories/{{repository_id}}/graph/search"),
        ("GET", f"{_V1}/repositories/{{repository_id}}/graph/neighborhood"),
        ("POST", f"{_V1}/repositories/{{repository_id}}/ask"),
    }
)


def enforce_demo_scope(request: Request, principal: Principal = Depends(require_service_token)) -> None:
    if not principal.is_demo:
        return
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if (request.method, template) not in DEMO_ALLOWED_ROUTES:
        raise APIError(403, "demo_forbidden")
    raw_id = request.path_params.get("repository_id")
    if raw_id is not None:
        if not (str(raw_id).isdigit() and int(raw_id) in config.demo_repository_ids()):
            raise APIError(404, "repository_not_found")


def demo_repository_filter(request: Request) -> Optional[FrozenSet[int]]:
    """For list routes: the repository ids this principal may see (None =
    no restriction, i.e. the operator)."""
    principal = getattr(request.state, "principal", None)
    return config.demo_repository_ids() if principal is not None and principal.is_demo else None
