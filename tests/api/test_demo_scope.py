"""The public demo principal (DEMO_API_SERVICE_TOKEN): deny-by-default,
read-only scope over DEMO_REPOSITORY_IDS, enforced in the backend.

The route guard enumerates the REAL application's routes, so a route added
later is denied to the demo until it is deliberately allowlisted in
api/demo_scope.py.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from api.demo_scope import DEMO_ALLOWED_ROUTES
from api.v1 import API_V1_PREFIX

from tests.api.conftest import TEST_SERVICE_TOKEN, auth_headers, insert_repository, requires_postgres
from tests.api.test_v1_auth import _PLACEHOLDER_PATH_PARAMS, _v1_routes
from tests.vectorstore.conftest import vector_store  # noqa: F401

DEMO_TOKEN = "demo-service-token-" + "d" * 40
BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"


def demo_headers():
    return auth_headers(DEMO_TOKEN)


@pytest.fixture
def demo_env(monkeypatch):
    monkeypatch.setenv("DEMO_API_SERVICE_TOKEN", DEMO_TOKEN)
    monkeypatch.setenv("DEMO_REPOSITORY_IDS", "1")


def _requests_with_repository(app, repository_id):
    for route in _v1_routes(app):
        path = route.path.replace("{repository_id}", str(repository_id))
        for name, value in _PLACEHOLDER_PATH_PARAMS.items():
            path = path.replace("{" + name + "}", value)
        for method in sorted(route.methods - {"HEAD"}):
            yield method, route.path, path


def test_every_route_outside_the_allowlist_is_forbidden_to_the_demo(api_client, app, demo_env):
    seen_allowed = set()
    for method, template, path in _requests_with_repository(app, 1):
        response = api_client.request(method, path, headers=demo_headers(), json={})
        if (method, template) in DEMO_ALLOWED_ROUTES:
            seen_allowed.add((method, template))
            assert response.json() != {"error": "demo_forbidden"}, (method, template)
        else:
            assert (response.status_code, response.json()) == (403, {"error": "demo_forbidden"}), (method, template)
    # No stale allowlist entry: each one is a real route.
    assert seen_allowed == set(DEMO_ALLOWED_ROUTES)


def test_the_allowlist_has_no_write_route_except_ask():
    writes = {(m, t) for m, t in DEMO_ALLOWED_ROUTES if m != "GET"}
    assert writes == {("POST", f"{API_V1_PREFIX}/repositories/{{repository_id}}/ask")}


@pytest.mark.parametrize(
    "method, path",
    [
        ("POST", "/ingestion-jobs"),
        ("POST", "/repositories/1/reindex"),
        ("POST", "/agent-tasks/00000000-0000-0000-0000-000000000000/decision"),
        ("POST", "/repositories/1/agent-tasks"),
        ("GET", "/agent-tasks"),
        ("GET", "/traces"),
        ("GET", "/ingestion-jobs"),
    ],
)
def test_consequential_and_operator_only_routes_are_refused(api_client, demo_env, method, path):
    response = api_client.request(method, f"{API_V1_PREFIX}{path}", headers=demo_headers(), json={"approved": True})
    assert (response.status_code, response.json()) == (403, {"error": "demo_forbidden"})


@pytest.mark.parametrize("header", [{"X-Role": "operator"}, {"X-Principal": "operator"}, {"X-Forwarded-User": "operator"}])
def test_the_role_cannot_be_chosen_by_a_header(api_client, demo_env, header):
    response = api_client.get(f"{API_V1_PREFIX}/traces", headers={**demo_headers(), **header})
    assert response.status_code == 403


@requires_postgres
def test_the_demo_sees_only_its_repositories_and_others_look_nonexistent(api_client, monkeypatch, vector_store, tmp_path):  # noqa: F811
    from tests.vectorstore.conftest import TEST_DATABASE_URL

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    demo_repo = insert_repository(vector_store, str(tmp_path / "demo"), name="demo-repo")
    private_repo = insert_repository(vector_store, str(tmp_path / "private"), name="private-repo")
    monkeypatch.setenv("DEMO_API_SERVICE_TOKEN", DEMO_TOKEN)
    monkeypatch.setenv("DEMO_REPOSITORY_IDS", str(demo_repo))

    listing = api_client.get(f"{API_V1_PREFIX}/repositories", headers=demo_headers()).json()
    assert [r["name"] for r in listing["repositories"]] == ["demo-repo"] and listing["total"] == 1

    hidden = api_client.get(f"{API_V1_PREFIX}/repositories/{private_repo}", headers=demo_headers())
    missing = api_client.get(f"{API_V1_PREFIX}/repositories/999999", headers=demo_headers())
    assert (hidden.status_code, hidden.json()) == (missing.status_code, missing.json()) == (404, {"error": "repository_not_found"})
    for sub_path in ("/files", "/graph/summary"):
        assert api_client.get(f"{API_V1_PREFIX}/repositories/{private_repo}{sub_path}", headers=demo_headers()).status_code == 404
    ask = api_client.post(f"{API_V1_PREFIX}/repositories/{private_repo}/ask", headers=demo_headers(), json={"question": "x"})
    assert ask.status_code == 404

    assert api_client.get(f"{API_V1_PREFIX}/repositories/{demo_repo}", headers=demo_headers()).status_code == 200

    # The operator is unaffected: sees both.
    operator = api_client.get(f"{API_V1_PREFIX}/repositories", headers=auth_headers()).json()
    assert {r["name"] for r in operator["repositories"]} == {"demo-repo", "private-repo"}


@pytest.mark.parametrize(
    "demo_token, ids",
    [
        ("short-demo-token", "1"),  # too short
        (TEST_SERVICE_TOKEN, "1"),  # same as the operator token
        (DEMO_TOKEN, ""),  # no demo repositories
        (DEMO_TOKEN, "1,abc"),  # malformed id list
    ],
)
def test_an_invalid_demo_configuration_never_accepts_the_demo_token(api_client, monkeypatch, demo_token, ids):
    monkeypatch.setenv("DEMO_API_SERVICE_TOKEN", demo_token)
    monkeypatch.setenv("DEMO_REPOSITORY_IDS", ids)
    response = api_client.get(f"{API_V1_PREFIX}/capabilities", headers=auth_headers(demo_token))
    if demo_token == TEST_SERVICE_TOKEN:
        # It is the operator token, so it authenticates as the OPERATOR, never as the demo.
        assert api_client.get(f"{API_V1_PREFIX}/traces", headers=auth_headers(demo_token)).status_code == 200
    else:
        assert response.status_code == 401
    assert api_client.get(f"{API_V1_PREFIX}/traces", headers=auth_headers()).status_code == 200


def test_without_a_demo_token_configured_demo_mode_is_off(api_client, monkeypatch):
    monkeypatch.delenv("DEMO_API_SERVICE_TOKEN", raising=False)
    assert api_client.get(f"{API_V1_PREFIX}/capabilities", headers=demo_headers()).status_code == 401


def _check_script(env):
    return subprocess.run(
        [sys.executable, str(BACKEND_DIR / "scripts" / "check_production_config.py")],
        cwd=BACKEND_DIR,
        env={"PATH": "/usr/bin:/bin", "DATABASE_URL": "postgresql://u@localhost/db", "API_SERVICE_TOKEN": TEST_SERVICE_TOKEN, **env},
        capture_output=True, text=True, timeout=30,
    )


def test_check_production_config_reports_demo_mode():
    assert "Demo mode: disabled" in _check_script({}).stdout
    ok = _check_script({"DEMO_API_SERVICE_TOKEN": DEMO_TOKEN, "DEMO_REPOSITORY_IDS": "3, 7"})
    assert ok.returncode == 0 and "Demo mode: enabled, repositories [3, 7]" in ok.stdout
    bad = _check_script({"DEMO_API_SERVICE_TOKEN": TEST_SERVICE_TOKEN, "DEMO_REPOSITORY_IDS": "3"})
    assert bad.returncode == 1 and "must differ from API_SERVICE_TOKEN" in bad.stdout
    assert DEMO_TOKEN not in ok.stdout and TEST_SERVICE_TOKEN not in bad.stdout
