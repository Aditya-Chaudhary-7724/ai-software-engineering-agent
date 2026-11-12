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
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import config
from api.errors import APIError

logger = logging.getLogger("api.auth")

API_TOKEN_ENV_VAR = "API_SERVICE_TOKEN"
DEMO_TOKEN_ENV_VAR = "DEMO_API_SERVICE_TOKEN"
# 32 characters of `secrets.token_urlsafe` output is ~190 bits of
# entropy — brute force is infeasible, so no lockout/rate limiting is
# needed for this one machine-to-machine credential.
MIN_TOKEN_LENGTH = 32

ROLE_OPERATOR = "operator"
ROLE_DEMO = "demo"

# auto_error=False: a missing/malformed header must produce this API's
# own `{"error": ...}` body, not Starlette's default 403 `{"detail": ...}`.
_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """Who the (trusted) frontend server is calling on behalf of. Decided
    ONLY by which secret token was presented, never by a header or body
    field the caller could set."""

    role: str

    @property
    def is_demo(self) -> bool:
        return self.role == ROLE_DEMO


def _configured_token() -> Optional[str]:
    token = os.environ.get(API_TOKEN_ENV_VAR, "")
    return token if len(token) >= MIN_TOKEN_LENGTH else None


def _configured_demo_token() -> Optional[str]:
    """The demo token, only if it is valid (fail closed: a short token, or
    one equal to the operator token, is never accepted)."""
    if not config.demo_mode_enabled():
        return None
    if config.demo_configuration_problems():
        logger.warning("%s is set but invalid (see check_production_config.py); demo requests are refused", DEMO_TOKEN_ENV_VAR)
        return None
    return os.environ[DEMO_TOKEN_ENV_VAR]


def _matches(presented: str, expected: Optional[str]) -> bool:
    # Constant-time comparison so response timing can't reveal how many
    # leading characters of a guess were correct.
    if not expected:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


def require_service_token(
    request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)
) -> Principal:
    expected = _configured_token()
    if expected is None:
        logger.warning("%s is unset or shorter than %d characters; refusing /api/v1 request", API_TOKEN_ENV_VAR, MIN_TOKEN_LENGTH)
        raise APIError(503, "api_auth_not_configured")

    presented = credentials.credentials if credentials is not None else ""
    is_operator = _matches(presented, expected)
    is_demo = _matches(presented, _configured_demo_token())  # always evaluated: no timing shortcut
    if not presented or not (is_operator or is_demo):
        raise APIError(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})
    principal = Principal(ROLE_OPERATOR if is_operator else ROLE_DEMO)
    request.state.principal = principal
    return principal
