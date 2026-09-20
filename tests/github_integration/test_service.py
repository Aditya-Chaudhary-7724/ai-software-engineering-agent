"""Unit tests for GitHubIntegrationService, with a fake GitOperations
(injected — the same dependency-injection style as this project's other
services) and the real GitHubAPIClient backed by monkeypatched
`requests` calls. Focuses on the authorization/approval gates: push and
pull-request creation must be impossible without an explicit, real
upstream decision.
"""

import requests
import pytest

from github_integration.exceptions import MissingCredentialsError, UnauthorizedActionError
from github_integration.git_operations import GitOperations
from github_integration.models import ClonedRepository, RepositoryReference
from github_integration.service import GitHubIntegrationService

_REPO_JSON = {
    "owner": {"login": "octocat"},
    "name": "Hello-World",
    "full_name": "octocat/Hello-World",
    "description": None,
    "default_branch": "main",
    "clone_url": "https://github.com/octocat/Hello-World.git",
    "private": False,
    "stargazers_count": 0,
}

_PRIVATE_REPO_JSON = {**_REPO_JSON, "private": True}


class _FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json_data = json_data or {}
        self.text = ""

    def json(self):
        return self._json_data


class _FakeGitOperations(GitOperations):
    def __init__(self):
        self.clone_calls = []
        self.branch_calls = []
        self.commit_calls = []
        self.push_calls = []

    def clone(self, clone_url, destination, token=None):
        self.clone_calls.append({"clone_url": clone_url, "destination": destination, "token": token})
        destination.mkdir(parents=True, exist_ok=True)

    def create_branch(self, repo_path, branch_name):
        self.branch_calls.append({"repo_path": repo_path, "branch_name": branch_name})

    def commit_all(self, repo_path, message, author_name, author_email):
        self.commit_calls.append(
            {"repo_path": repo_path, "message": message, "author_name": author_name, "author_email": author_email}
        )
        return "deadbeef"

    def push(self, repo_path, branch_name, token, remote="origin"):
        self.push_calls.append({"repo_path": repo_path, "branch_name": branch_name, "token": token})


def _sample_cloned() -> ClonedRepository:
    return ClonedRepository(
        reference=RepositoryReference(owner="octocat", repo="Hello-World", url="https://github.com/octocat/Hello-World"),
        local_path="/tmp/does-not-matter",
        default_branch="main",
    )


def test_clone_repository_resolves_destination_and_clones(tmp_path, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(200, _REPO_JSON))
    fake_git = _FakeGitOperations()
    service = GitHubIntegrationService(token=None, git_operations=fake_git)

    cloned = service.clone_repository("https://github.com/octocat/Hello-World", str(tmp_path))

    assert cloned.local_path == str((tmp_path / "octocat" / "Hello-World").resolve())
    assert cloned.default_branch == "main"
    assert fake_git.clone_calls == [
        {
            "clone_url": "https://github.com/octocat/Hello-World.git",
            "destination": tmp_path.resolve() / "octocat" / "Hello-World",
            "token": None,
        }
    ]


def test_clone_repository_passes_token_only_for_private_repos(tmp_path, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(200, _PRIVATE_REPO_JSON))
    fake_git = _FakeGitOperations()
    service = GitHubIntegrationService(token="a-real-token", git_operations=fake_git)

    service.clone_repository("https://github.com/octocat/Hello-World", str(tmp_path))

    assert fake_git.clone_calls[0]["token"] == "a-real-token"


def test_clone_repository_omits_token_for_public_repos_even_if_one_is_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(200, _REPO_JSON))
    fake_git = _FakeGitOperations()
    service = GitHubIntegrationService(token="a-real-token", git_operations=fake_git)

    service.clone_repository("https://github.com/octocat/Hello-World", str(tmp_path))

    assert fake_git.clone_calls[0]["token"] is None


def test_push_branch_refuses_without_explicit_authorization():
    fake_git = _FakeGitOperations()
    service = GitHubIntegrationService(token="a-real-token", git_operations=fake_git)

    with pytest.raises(UnauthorizedActionError):
        service.push_branch(_sample_cloned(), "fix-branch", authorized=False)

    assert fake_git.push_calls == []  # nothing was actually pushed


def test_push_branch_requires_a_token_even_when_authorized():
    fake_git = _FakeGitOperations()
    service = GitHubIntegrationService(token=None, git_operations=fake_git)

    with pytest.raises(MissingCredentialsError):
        service.push_branch(_sample_cloned(), "fix-branch", authorized=True)

    assert fake_git.push_calls == []


def test_push_branch_pushes_when_authorized_and_token_present():
    fake_git = _FakeGitOperations()
    service = GitHubIntegrationService(token="a-real-token", git_operations=fake_git)

    service.push_branch(_sample_cloned(), "fix-branch", authorized=True)

    assert len(fake_git.push_calls) == 1
    call = fake_git.push_calls[0]
    assert str(call["repo_path"]) == "/tmp/does-not-matter"
    assert call["branch_name"] == "fix-branch"
    assert call["token"] == "a-real-token"


def test_create_pull_request_refuses_without_human_approval():
    service = GitHubIntegrationService(token="a-real-token", git_operations=_FakeGitOperations())

    with pytest.raises(UnauthorizedActionError):
        service.create_pull_request(
            _sample_cloned(), "fix-branch", "Fix the bug", "Details", approved=False
        )


def test_create_pull_request_creates_when_approved(monkeypatch):
    captured = {}

    def _fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _FakeResponse(201, {"number": 7, "html_url": "https://github.com/octocat/Hello-World/pull/7", "state": "open"})

    monkeypatch.setattr(requests, "post", _fake_post)
    service = GitHubIntegrationService(token="a-real-token", git_operations=_FakeGitOperations())

    result = service.create_pull_request(
        _sample_cloned(), "fix-branch", "Fix the bug", "Details", approved=True
    )

    assert result.number == 7
    assert captured["json"]["base"] == "main"  # defaults to the cloned repo's default branch
    assert captured["json"]["head"] == "fix-branch"


def test_commit_all_requires_explicit_author_identity(tmp_path):
    fake_git = _FakeGitOperations()
    service = GitHubIntegrationService(token=None, git_operations=fake_git)

    service.commit_all(_sample_cloned(), "message", author_name="A Human", author_email="human@example.com")

    assert fake_git.commit_calls[0]["author_name"] == "A Human"
    assert fake_git.commit_calls[0]["author_email"] == "human@example.com"
