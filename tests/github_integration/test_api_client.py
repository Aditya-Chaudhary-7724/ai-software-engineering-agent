"""Unit tests for GitHubAPIClient with `requests.get`/`requests.post`
monkeypatched — no real network calls (see test_github_integration.py
for real-network tests). Same monkeypatching style as
vectorstore/embeddings/openai_provider's tests: verifies request/response
handling, not that a live API call succeeds.
"""

import requests

from github_integration import api_client
from github_integration.exceptions import GitHubAPIError, MissingCredentialsError


class _FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


_REPO_JSON = {
    "owner": {"login": "octocat"},
    "name": "Hello-World",
    "full_name": "octocat/Hello-World",
    "description": "My first repository on GitHub!",
    "default_branch": "master",
    "clone_url": "https://github.com/octocat/Hello-World.git",
    "private": False,
    "stargazers_count": 1000,
}


def test_get_repository_sends_authorization_header_when_token_given(monkeypatch):
    captured = {}

    def _fake_get(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _FakeResponse(200, _REPO_JSON)

    monkeypatch.setattr(requests, "get", _fake_get)

    client = api_client.GitHubAPIClient(token="secret-token")
    metadata = client.get_repository("octocat", "Hello-World")

    assert captured["url"] == "https://api.github.com/repos/octocat/Hello-World"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert metadata.owner == "octocat"
    assert metadata.default_branch == "master"
    assert metadata.private is False


def test_get_repository_omits_authorization_header_when_no_token(monkeypatch):
    captured = {}

    def _fake_get(url, headers, timeout):
        captured["headers"] = headers
        return _FakeResponse(200, _REPO_JSON)

    monkeypatch.setattr(requests, "get", _fake_get)

    api_client.GitHubAPIClient(token=None).get_repository("octocat", "Hello-World")

    assert "Authorization" not in captured["headers"]


def test_get_repository_404_raises_clear_error(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(404, text="Not Found"))

    try:
        api_client.GitHubAPIClient(token=None).get_repository("nobody", "nothing")
        assert False, "should have raised"
    except GitHubAPIError as exc:
        assert "not found" in str(exc).lower()


def test_get_repository_other_error_status_raises(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(500, text="server error"))

    try:
        api_client.GitHubAPIClient(token=None).get_repository("octocat", "Hello-World")
        assert False, "should have raised"
    except GitHubAPIError as exc:
        assert "500" in str(exc)


def test_create_pull_request_requires_a_token():
    client = api_client.GitHubAPIClient(token=None)
    try:
        client.create_pull_request("octocat", "Hello-World", head="fix", base="main", title="t", body="b")
        assert False, "should have raised"
    except MissingCredentialsError:
        pass


def test_create_pull_request_sends_expected_payload(monkeypatch):
    captured = {}

    def _fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse(201, {"number": 42, "html_url": "https://github.com/octocat/Hello-World/pull/42", "state": "open"})

    monkeypatch.setattr(requests, "post", _fake_post)

    client = api_client.GitHubAPIClient(token="secret-token")
    result = client.create_pull_request(
        "octocat", "Hello-World", head="fix-branch", base="main", title="Fix the bug", body="Details here"
    )

    assert captured["url"] == "https://api.github.com/repos/octocat/Hello-World/pulls"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["json"] == {"title": "Fix the bug", "head": "fix-branch", "base": "main", "body": "Details here"}
    assert result.number == 42
    assert result.html_url.endswith("/pull/42")


def test_create_pull_request_error_response_raises(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(422, text="validation failed"))

    client = api_client.GitHubAPIClient(token="secret-token")
    try:
        client.create_pull_request("octocat", "Hello-World", head="x", base="main", title="t", body="b")
        assert False, "should have raised"
    except GitHubAPIError as exc:
        assert "422" in str(exc)


def test_token_never_appears_in_string_representation_of_client():
    client = api_client.GitHubAPIClient(token="super-secret-value")
    assert "super-secret-value" not in repr(client)
    assert "super-secret-value" not in str(client)
