"""Capabilities endpoint: honest booleans, never configuration values."""

from api.v1 import API_V1_PREFIX

from tests.api.conftest import auth_headers

_FAKE_SECRET = "definitely-a-secret-value-98765"


def test_capabilities_report_booleans_and_never_secret_values(api_client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"postgresql://user:{_FAKE_SECRET}@127.0.0.1:1/db")
    monkeypatch.setenv("LLM_API_KEY", _FAKE_SECRET)
    monkeypatch.setenv("GITHUB_TOKEN", _FAKE_SECRET)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("NEO4J_URI", raising=False)

    response = api_client.get(f"{API_V1_PREFIX}/capabilities", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert _FAKE_SECRET not in response.text
    assert body["api_version"] == "v1"
    assert body["database"] == {"ok": False, "detail": "unreachable"}
    assert body["graph"] == {"ok": True, "detail": "not configured (optional)"}
    assert body["llm_provider_configured"] is True
    assert body["embedding_provider_configured"] is False
    assert body["github_token_configured"] is True
    assert isinstance(body["sandbox_docker_available"], bool)
    assert isinstance(body["sandbox_image_available"], bool)
    if not body["sandbox_docker_available"]:
        assert body["sandbox_image_available"] is False


def test_capabilities_report_exposed_operations(api_client):
    """Without a running ingestion worker (TestClient doesn't run the
    lifespan) ingestion is reported unavailable rather than assumed."""
    features = api_client.get(f"{API_V1_PREFIX}/capabilities", headers=auth_headers()).json()["features"]

    for read_only in ("repository_browsing", "file_browsing", "graph_summary", "trace_viewing"):
        assert features[read_only] is True
    assert features["repository_ingestion"] is False
    assert features["ask"] is True  # F4: read-only Q&A
    for not_exposed in ("agent_execution", "approvals", "test_execution", "evaluation_runs", "github_push_and_pull_requests"):
        assert features[not_exposed] is False


# F3: the ONLY state-changing routes under /api/v1. Adding another one must
# be a deliberate change to this list.
ALLOWED_WRITE_ROUTES = {
    ("POST", "/api/v1/ingestion-jobs"),
    ("POST", "/api/v1/repositories/{repository_id}/reindex"),
}
# F4: POST only so the question travels in the body, not URLs/logs. Proven
# read-only by tests/api/test_v1_ask.py::test_ask_is_strictly_read_only.
READ_ONLY_POST_ROUTES = {
    ("POST", "/api/v1/repositories/{repository_id}/ask"),
}


def test_only_the_allowlisted_routes_use_non_get_methods(app):
    from fastapi.routing import APIRoute

    v1_routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(API_V1_PREFIX)]
    non_get = {(m, r.path) for r in v1_routes for m in r.methods if m not in {"GET", "HEAD"}}
    assert non_get == ALLOWED_WRITE_ROUTES | READ_ONLY_POST_ROUTES


def test_no_agent_approval_or_github_write_routes_exist(app):
    from fastapi.routing import APIRoute

    for route in [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(API_V1_PREFIX)]:
        for forbidden in ("agent", "approv", "resume", "push", "pull", "evaluat", "sandbox", "/run"):
            assert forbidden not in route.path, route.path
