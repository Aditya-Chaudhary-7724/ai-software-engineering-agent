"""Shared fixtures for github_integration tests.

Real-network tests are marked `requires_network` and skip cleanly
(rather than failing) when api.github.com isn't reachable — same
"LOCAL TESTED vs REQUIRES EXTERNAL SERVICE" pattern as
`requires_postgres`/`requires_neo4j`/`requires_docker` elsewhere in
this project. They hit a small, stable, well-known public repository
(`octocat/Hello-World` — GitHub's own canonical example repo, created
for exactly this kind of demonstration) and never authenticate, so no
GITHUB_TOKEN is required to run them.
"""

import requests as requests_lib
import pytest

PUBLIC_TEST_REPO_URL = "https://github.com/octocat/Hello-World"


# F8: the unauthenticated GitHub API allows 60 requests/hour per IP, and
# CI runners share IPs. The real-network tests need a few calls each, so
# with less quota than this they skip (honestly, with the reason) instead
# of failing with "403 API rate limit exceeded" — which says nothing about
# this project's code. Only quota exhaustion is skipped; any other failure
# still fails.
MIN_REMAINING_GITHUB_REQUESTS = 15


def _github_unavailable_reason() -> str:
    """"" when the real-network tests can run, else why they cannot."""
    try:
        # One request answers both questions, and /rate_limit itself does
        # not count against the quota (a plain GET https://api.github.com does).
        response = requests_lib.get("https://api.github.com/rate_limit", timeout=5)
        if response.status_code >= 500:
            return "api.github.com not reachable (REQUIRES EXTERNAL SERVICE)"
        remaining = int(response.json()["resources"]["core"]["remaining"])
    except requests_lib.RequestException:
        return "api.github.com not reachable (REQUIRES EXTERNAL SERVICE)"
    except (ValueError, KeyError, TypeError):
        return ""  # unexpected shape: run the tests and let them report
    if remaining < MIN_REMAINING_GITHUB_REQUESTS:
        return f"GitHub API rate limit nearly exhausted for this IP ({remaining} left) (REQUIRES EXTERNAL SERVICE)"
    return ""


_UNAVAILABLE = _github_unavailable_reason()
requires_network = pytest.mark.skipif(bool(_UNAVAILABLE), reason=_UNAVAILABLE or "available")
