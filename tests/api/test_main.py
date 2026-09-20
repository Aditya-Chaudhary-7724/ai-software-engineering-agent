"""Tests for the real FastAPI application (Phase 15) — using
`TestClient`, which runs the actual ASGI app in-process (not mocked),
including the real `/health/*` routes wired to the real `api.health`
module.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from tests.vectorstore.conftest import TEST_DATABASE_URL, requires_postgres


def _fresh_app():
    """Reloads api.main so each test gets a fresh FastAPI instance —
    otherwise module-level `app = create_app()` would be built once
    against whatever environment was active at IMPORT time, before
    a test's own monkeypatched env vars (e.g. APP_ENV) took effect.
    """
    import api.main as main_module

    importlib.reload(main_module)
    return main_module.app


def test_root_endpoint_reports_service_status():
    client = TestClient(_fresh_app())
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"service": "ai-software-engineering-agent", "status": "ok"}


def test_liveness_endpoint_returns_200():
    client = TestClient(_fresh_app())
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


@requires_postgres
def test_readiness_endpoint_returns_200_when_ready(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    client = TestClient(_fresh_app())

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_readiness_endpoint_returns_503_when_not_ready(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://baduser@127.0.0.1:1/nope")
    client = TestClient(_fresh_app())

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["ready"] is False


def test_docs_are_disabled_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    client = TestClient(_fresh_app())

    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_docs_are_available_outside_production(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    client = TestClient(_fresh_app())

    assert client.get("/docs").status_code == 200


def test_unhandled_exception_never_leaks_internal_detail_in_the_response():
    """A route that raises must produce a fixed, generic error body —
    never a stack trace, exception message, or file path — regardless
    of what the underlying exception says.
    """
    app = _fresh_app()

    @app.get("/__boom_for_test__")
    async def _boom():
        raise RuntimeError("leaked secret: DATABASE_PASSWORD=hunter2 at /etc/secret/path")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/__boom_for_test__")

    assert response.status_code == 500
    body_text = response.text
    assert "hunter2" not in body_text
    assert "/etc/secret" not in body_text
    assert "RuntimeError" not in body_text
    assert response.json() == {"error": "internal_server_error"}


def test_cors_is_disabled_by_default_with_no_allowed_origins(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    client = TestClient(_fresh_app())

    response = client.get("/health/live", headers={"Origin": "https://example.com"})

    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_only_explicitly_configured_origins(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://allowed.example.com")
    client = TestClient(_fresh_app())

    allowed_response = client.get("/health/live", headers={"Origin": "https://allowed.example.com"})
    disallowed_response = client.get("/health/live", headers={"Origin": "https://attacker.example.com"})

    assert allowed_response.headers.get("access-control-allow-origin") == "https://allowed.example.com"
    assert "access-control-allow-origin" not in disallowed_response.headers
