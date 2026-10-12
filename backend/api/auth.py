"""Service-to-service authentication for `/api/v1`.

The only intended caller of this API is the (future, F2) Next.js server,
which holds `API_SERVICE_TOKEN` in a server-only environment variable
and authenticates the human operator itself (password hash + session
cookie). The browser never sees this token and never calls this API
directly — which is also why CORS stays closed.

Fail-closed: if `API_SERVICE_TOKEN` is unset or too short to be a real
random secret, every `/api/v1` route refuses to serve (503), rather than
silently running unauthenticated.
"""

import hmac
import logging
import os
from typing import Optional

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.errors import APIError

logger = logging.getLogger("api.auth")

API_TOKEN_ENV_VAR = "API_SERVICE_TOKEN"
# 32 characters of `secrets.token_urlsafe` output is ~190 bits of
# entropy — brute force is infeasible, so no lockout/rate limiting is
# needed for this one machine-to-machine credential.
MIN_TOKEN_LENGTH = 32

# auto_error=False: a missing/malformed header must produce this API's
# own `{"error": ...}` body, not Starlette's default 403 `{"detail": ...}`.
_bearer_scheme = HTTPBearer(auto_error=False)


def _configured_token() -> Optional[str]:
    token = os.environ.get(API_TOKEN_ENV_VAR, "")
    return token if len(token) >= MIN_TOKEN_LENGTH else None


def require_service_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)) -> None:
    expected = _configured_token()
    if expected is None:
        logger.warning("%s is unset or shorter than %d characters; refusing /api/v1 request", API_TOKEN_ENV_VAR, MIN_TOKEN_LENGTH)
        raise APIError(503, "api_auth_not_configured")

    presented = credentials.credentials if credentials is not None else ""
    # Constant-time comparison so response timing can't reveal how many
    # leading characters of a guess were correct.
    if not presented or not hmac.compare_digest(presented.encode(), expected.encode()):
        raise APIError(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})
