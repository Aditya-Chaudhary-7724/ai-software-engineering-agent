"""Assembles `/api/v1`.

Authentication is attached ONCE, at this router, so it applies to every
route included below — an individual route module cannot forget it.
`tests/api/test_v1_auth.py` additionally enumerates the application's
real routes and fails if any `/api/v1` route is reachable without it.

Write operations are limited to enqueuing ingestion/re-index jobs (F3,
routes/jobs.py). Ask (F4, routes/ask.py) is a read-only POST. No agent
execution, approval, test execution, or GitHub push/PR endpoint exists here.
"""

from fastapi import APIRouter, Depends

from api.auth import require_service_token
from api.routes import ask, capabilities, files, graph, jobs, repositories, traces

API_V1_PREFIX = "/api/v1"


def build_v1_router() -> APIRouter:
    router = APIRouter(prefix=API_V1_PREFIX, dependencies=[Depends(require_service_token)])
    for module in (capabilities, repositories, files, graph, traces, jobs, ask):
        router.include_router(module.router)
    return router
