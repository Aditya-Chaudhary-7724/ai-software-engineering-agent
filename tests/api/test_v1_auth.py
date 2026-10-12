"""Authentication for `/api/v1` — including the regression guard that
no route under that prefix can be added without it.

The guard enumerates the REAL application's routes (not a hand-kept
list), so a future route added to any router is covered automatically.
It checks two independent things: (1) structurally, the auth dependency
is in every route's dependency tree; (2) behaviorally, every route
actually refuses an unauthenticated request.
"""

import logging
import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api.auth import MIN_TOKEN_LENGTH, require_service_token
from api.main import create_app
from api.v1 import API_V1_PREFIX

from tests.api.conftest import TEST_SERVICE_TOKEN, auth_headers

_PLACEHOLDER_PATH_PARAMS = {"repository_id": "1", "trace_id": str(uuid.uuid4()), "job_id": str(uuid.uuid4())}


def _v1_routes(application):
    routes = [r for r in application.routes if isinstance(r, APIRoute) and r.path.startswith(API_V1_PREFIX)]
    # Guard against this test passing vacuously if routing changes.
    assert len(routes) >= 8
    return routes


def _concrete_requests(application):
    for route in _v1_routes(application):
        path = route.path
        for name, value in _PLACEHOLDER_PATH_PARAMS.items():
            path = path.replace("{" + name + "}", value)
        assert "{" not in path, f"add a placeholder for a new path parameter in {route.path}"
        for method in sorted(route.methods - {"HEAD"}):
            yield method, path


def _dependency_calls(dependant):
    for sub in dependant.dependencies:
        yield sub.call
        yield from _dependency_calls(sub)


def test_every_v1_route_declares_the_service_token_dependency(app):
    for route in _v1_routes(app):
        assert require_service_token in set(_dependency_calls(route.dependant)), route.path


def test_every_v1_route_rejects_a_request_without_credentials(api_client, app):
    for method, path in _concrete_requests(app):
        response = api_client.request(method, path)
        assert response.status_code == 401, (method, path, response.status_code)
        assert response.json() == {"error": "unauthorized"}
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer wrong-token-" + "y" * 40},
        {"Authorization": f"Bearer {TEST_SERVICE_TOKEN}x"},
        {"Authorization": f"Bearer {TEST_SERVICE_TOKEN[:-1]}"},
        {"Authorization": f"Basic {TEST_SERVICE_TOKEN}"},
        {"Authorization": TEST_SERVICE_TOKEN},
        {"Authorization": "Bearer "},
        {"X-API-Key": TEST_SERVICE_TOKEN},
    ],
)
def test_every_v1_route_rejects_wrong_or_malformed_credentials(api_client, app, headers):
    for method, path in _concrete_requests(app):
        assert api_client.request(method, path, headers=headers).status_code == 401, (method, path)


def test_token_in_query_string_is_not_accepted(api_client):
    response = api_client.get(f"{API_V1_PREFIX}/capabilities", params={"token": TEST_SERVICE_TOKEN})
    assert response.status_code == 401


def test_correct_token_is_accepted(api_client):
    assert api_client.get(f"{API_V1_PREFIX}/traces", headers=auth_headers()).status_code == 200


@pytest.mark.parametrize("configured", [None, "", "short-token", "z" * (MIN_TOKEN_LENGTH - 1)])
def test_api_fails_closed_when_token_is_unset_or_too_short(monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("API_SERVICE_TOKEN", raising=False)
    else:
        monkeypatch.setenv("API_SERVICE_TOKEN", configured)
    client = TestClient(create_app())

    # Even a request presenting exactly the (weak) configured value is refused.
    headers = {"Authorization": f"Bearer {configured}"} if configured else {}
    response = client.get(f"{API_V1_PREFIX}/traces", headers=headers)

    assert response.status_code == 503
    assert response.json() == {"error": "api_auth_not_configured"}


def test_health_endpoints_remain_unauthenticated(api_client, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert api_client.get("/").status_code == 200
    assert api_client.get("/health/live").json() == {"status": "alive"}
    # Unready (no DB) — but answered without credentials, as before F1.
    assert api_client.get("/health/ready").status_code == 503


def test_token_value_never_appears_in_responses_or_logs(api_client, app, caplog):
    caplog.set_level(logging.DEBUG)
    wrong = "Bearer attacker-guess-" + "q" * 40
    for method, path in _concrete_requests(app):
        response = api_client.request(method, path, headers={"Authorization": wrong})
        assert TEST_SERVICE_TOKEN not in response.text
        assert "attacker-guess" not in response.text
    assert TEST_SERVICE_TOKEN not in caplog.text
    assert "attacker-guess" not in caplog.text


def test_cors_stays_closed_for_api_v1(api_client):
    response = api_client.get(
        f"{API_V1_PREFIX}/traces", headers={**auth_headers(), "Origin": "https://attacker.example.com"}
    )
    assert "access-control-allow-origin" not in response.headers

    preflight = api_client.options(
        f"{API_V1_PREFIX}/traces",
        headers={"Origin": "https://attacker.example.com", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in preflight.headers


def test_api_v1_responses_are_never_cacheable(api_client):
    for headers in ({}, auth_headers()):
        response = api_client.get(f"{API_V1_PREFIX}/traces", headers=headers)
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"


def test_the_guard_detects_a_route_registered_without_authentication():
    """Proves the two guards above are not vacuous: a route mounted under
    /api/v1 OUTSIDE the authenticated router is flagged by both."""
    application = create_app()

    @application.get(f"{API_V1_PREFIX}/__unprotected_for_test__")
    def _unprotected() -> dict:
        return {"leaked": True}

    unprotected = [
        r.path
        for r in _v1_routes(application)
        if require_service_token not in set(_dependency_calls(r.dependant))
    ]
    assert unprotected == [f"{API_V1_PREFIX}/__unprotected_for_test__"]
    assert TestClient(application).get(f"{API_V1_PREFIX}/__unprotected_for_test__").status_code == 200
