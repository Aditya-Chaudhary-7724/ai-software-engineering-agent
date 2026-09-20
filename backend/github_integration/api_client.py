"""Thin wrapper over the GitHub REST API (api.github.com).

No PyGithub/githubkit SDK: this package only ever needs two calls (read
a repository's metadata, open a pull request), and GitHub's REST API is
plain HTTP + JSON — `requests` (already a transitive dependency in this
project's environment, and the de facto standard synchronous Python
HTTP client) gives well-tested timeout/error handling without pulling
in a heavier SDK that would hide exactly the two calls this project
actually needs.

Authentication is `GITHUB_TOKEN` only, sent as a bearer token in the
`Authorization` header — never as a query parameter (which URL-logs
could capture) and never printed or logged anywhere in this module. A
missing token is accepted for read-only calls against public
repositories (GitHub's REST API allows unauthenticated GETs, subject to
a much lower rate limit); `create_pull_request` always requires one.
"""

from typing import Optional

import requests

from github_integration.exceptions import GitHubAPIError, MissingCredentialsError
from github_integration.models import PullRequestResult, RepositoryMetadata

API_BASE_URL = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = 15


class GitHubAPIClient:
    def __init__(self, token: Optional[str]) -> None:
        self._token = token

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def get_repository(self, owner: str, repo: str) -> RepositoryMetadata:
        response = requests.get(
            f"{API_BASE_URL}/repos/{owner}/{repo}", headers=self._headers(), timeout=REQUEST_TIMEOUT_SECONDS
        )
        if response.status_code == 404:
            raise GitHubAPIError(
                f"Repository '{owner}/{repo}' was not found (or is private and no valid GITHUB_TOKEN was provided)."
            )
        if not response.ok:
            raise GitHubAPIError(
                f"GitHub API error fetching '{owner}/{repo}': {response.status_code} {response.text[:200]}"
            )

        data = response.json()
        return RepositoryMetadata(
            owner=data["owner"]["login"],
            name=data["name"],
            full_name=data["full_name"],
            description=data.get("description"),
            default_branch=data["default_branch"],
            clone_url=data["clone_url"],
            private=data["private"],
            stargazers_count=data["stargazers_count"],
        )

    def create_pull_request(self, owner: str, repo: str, head: str, base: str, title: str, body: str) -> PullRequestResult:
        if not self._token:
            raise MissingCredentialsError("GITHUB_TOKEN is required to create a pull request.")

        response = requests.post(
            f"{API_BASE_URL}/repos/{owner}/{repo}/pulls",
            headers=self._headers(),
            json={"title": title, "head": head, "base": base, "body": body},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if not response.ok:
            raise GitHubAPIError(
                f"GitHub API error creating a pull request on '{owner}/{repo}': "
                f"{response.status_code} {response.text[:300]}"
            )

        data = response.json()
        return PullRequestResult(number=data["number"], html_url=data["html_url"], state=data["state"])
