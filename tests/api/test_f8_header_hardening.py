"""F8: response-header guarantees that must not depend on client-controlled
input.

Starlette < 1.3 rebuilds `request.url` from the Host header plus the path
(PYSEC-2026-161 / PYSEC-2026-248, unfixed in the pinned starlette 0.41.3).
A forged Host such as `evil/x?` turns `request.url.path` into `/x`, so a
check written against `request.url.path` can be skipped while the router —
which matches on the raw ASGI `scope["path"]` — still serves `/api/v1/...`.
Authentication is attached to the router and was never bypassable; the
`Cache-Control: no-store` header on responses that carry source code was.
"""

import pytest

from tests.api.conftest import auth_headers


@pytest.mark.parametrize("host", ["testserver", "evil.example/x?", "evil.example/x#", "a@evil.example"])
def test_api_v1_responses_are_never_cacheable_whatever_the_host_header(api_client, host):
    response = api_client.get("/api/v1/capabilities", headers={**auth_headers(), "host": host})
    assert response.headers.get("cache-control") == "no-store"
    assert response.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.parametrize("host", ["evil.example/x?", "evil.example/x#"])
def test_a_forged_host_header_still_cannot_skip_authentication(api_client, host):
    response = api_client.get("/api/v1/capabilities", headers={"host": host})
    assert response.status_code == 401
    assert response.headers.get("cache-control") == "no-store"


def test_enabled_cors_allows_only_the_headers_an_api_call_needs(monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    import api.main as main_module

    monkeypatch.setenv("ALLOWED_ORIGINS", "https://allowed.example.com")
    reloaded = importlib.reload(main_module)
    try:
        client = TestClient(reloaded.create_app())
        preflight = {"Origin": "https://allowed.example.com", "Access-Control-Request-Method": "POST"}
        ok = client.options("/api/v1/capabilities", headers={**preflight, "Access-Control-Request-Headers": "authorization,content-type"})
        bad = client.options("/api/v1/capabilities", headers={**preflight, "Access-Control-Request-Headers": "x-anything"})
        assert ok.status_code == 200
        assert bad.status_code == 400
    finally:
        monkeypatch.delenv("ALLOWED_ORIGINS")
        importlib.reload(main_module)


@pytest.mark.parametrize("authenticated", [True, False])
def test_no_redirect_ever_reflects_the_host_header(api_client, authenticated):
    """Trailing-slash redirects used to send `Location: http://<Host>/...`,
    even without a token. They are disabled: a trailing slash is a 404 (or
    401 without a token), never a redirect to a client-chosen host."""
    headers = {"host": "evil.example", **(auth_headers() if authenticated else {})}
    response = api_client.get("/api/v1/capabilities/", headers=headers, follow_redirects=False)
    assert not 300 <= response.status_code < 400
    assert "location" not in response.headers
    assert response.headers.get("cache-control") == "no-store"
