"""One error shape for every `/api/v1` failure: `{"error": "<code>"}`.

The same fixed-body principle as `main.py`'s unhandled-exception
handler, applied to EXPECTED failures too: a response carries a stable,
machine-readable code — never an exception message, which in this
codebase can contain an absolute server path (e.g. `NoTestCommandError`,
`InvalidRepositoryPathError`) or echo caller input.
"""

from typing import Dict, Optional

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        headers: Optional[Dict[str, str]] = None,
        details: Optional[Dict[str, int]] = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.headers = headers
        # Optional machine-readable context, restricted to integers (e.g. the
        # id of the repository that already exists) — never free text.
        self.details = details or {}


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, APIError)
    content = {"error": exc.code, **{k: v for k, v in exc.details.items() if isinstance(v, int)}}
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # FastAPI's default 422 body echoes the rejected input back. Report
    # only WHICH fields were invalid (their location names), never the
    # submitted values.
    assert isinstance(exc, RequestValidationError)
    fields = sorted({".".join(str(part) for part in error.get("loc", ())) for error in exc.errors()})
    return JSONResponse(status_code=422, content={"error": "invalid_request", "fields": fields})
