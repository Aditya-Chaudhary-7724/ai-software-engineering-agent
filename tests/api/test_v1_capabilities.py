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
    # F6: agent tasks/approvals exist, but are reported available only while
    # the agent worker runs — never assumed.
    for worker_dependent in ("agent_execution", "approvals"):
        assert features[worker_dependent] is False
    for not_exposed in ("test_execution", "evaluation_runs", "github_push_and_pull_requests"):
        assert features[not_exposed] is False


def test_agent_features_follow_the_agent_worker(app, api_client):
    class RunningWorker:
        is_running = True

    app.state.agent_worker = RunningWorker()
    features = api_client.get(f"{API_V1_PREFIX}/capabilities", headers=auth_headers()).json()["features"]
    assert features["agent_execution"] is True and features["approvals"] is True
    assert features["github_push_and_pull_requests"] is False


# F3: the ONLY state-changing routes under /api/v1. Adding another one must
# be a deliberate change to this list.
ALLOWED_WRITE_ROUTES = {
    ("POST", "/api/v1/ingestion-jobs"),
    ("POST", "/api/v1/repositories/{repository_id}/reindex"),
    # F6: enqueue an agent task; record ONE bound approval decision.
    ("POST", "/api/v1/repositories/{repository_id}/agent-tasks"),
    ("POST", "/api/v1/agent-tasks/{task_id}/decision"),
}
# F4: POST only so the question travels in the body, not URLs/logs. Proven
# read-only by tests/api/test_v1_ask.py::test_ask_is_strictly_read_only.
READ_ONLY_POST_ROUTES = {
    ("POST", "/api/v1/repositories/{repository_id}/ask"),
    # F6: pure classification of the instruction text; touches nothing.
    ("POST", "/api/v1/agent-tasks/route-preview"),
}


def test_only_the_allowlisted_routes_use_non_get_methods(app):
    from fastapi.routing import APIRoute

    v1_routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(API_V1_PREFIX)]
    non_get = {(m, r.path) for r in v1_routes for m in r.methods if m not in {"GET", "HEAD"}}
    assert non_get == ALLOWED_WRITE_ROUTES | READ_ONLY_POST_ROUTES


# F6 deliberately adds agent-task and approval routes (allowlisted above);
# these are exactly the agent-related paths that may exist.
AGENT_ROUTE_PATHS = {
    "/api/v1/agent-tasks",
    "/api/v1/agent-tasks/route-preview",
    "/api/v1/agent-tasks/{task_id}",
    "/api/v1/agent-tasks/{task_id}/decision",
    "/api/v1/repositories/{repository_id}/agent-tasks",
}


def test_no_github_write_or_unlisted_agent_routes_exist(app):
    from fastapi.routing import APIRoute

    for route in [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(API_V1_PREFIX)]:
        for forbidden in ("resume", "push", "pull", "evaluat", "sandbox", "/run", "approv", "commit", "merge"):
            assert forbidden not in route.path, route.path
        if "agent" in route.path or "decision" in route.path:
            assert route.path in AGENT_ROUTE_PATHS, route.path


def test_capabilities_name_the_llm_provider_and_flag_a_misconfiguration(api_client, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    body = api_client.get(f"{API_V1_PREFIX}/capabilities", headers=auth_headers()).json()
    assert (body["llm_provider"], body["llm_provider_configured"]) == ("stub", False)

    monkeypatch.setenv("LLM_PROVIDER", "groq")
    body = api_client.get(f"{API_V1_PREFIX}/capabilities", headers=auth_headers()).json()
    assert (body["llm_provider"], body["llm_provider_configured"]) == ("misconfigured", False)

    monkeypatch.setenv("GROQ_API_KEY", _FAKE_SECRET)
    monkeypatch.setenv("LLM_MODEL", "vendor/model-id")
    response = api_client.get(f"{API_V1_PREFIX}/capabilities", headers=auth_headers())
    assert (response.json()["llm_provider"], response.json()["llm_provider_configured"]) == ("groq", True)
    assert _FAKE_SECRET not in response.text


def test_capabilities_name_the_embedding_provider(api_client, monkeypatch):
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    body = api_client.get(f"{API_V1_PREFIX}/capabilities", headers=auth_headers()).json()
    assert (body["embedding_provider"], body["embedding_provider_configured"]) == ("deterministic_local", False)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fastembed")
    body = api_client.get(f"{API_V1_PREFIX}/capabilities", headers=auth_headers()).json()
    assert (body["embedding_provider"], body["embedding_provider_configured"]) == ("fastembed", True)
    monkeypatch.setenv("EMBEDDING_MODEL", "not/allowlisted")
    body = api_client.get(f"{API_V1_PREFIX}/capabilities", headers=auth_headers()).json()
    assert body["embedding_provider"] == "misconfigured"
