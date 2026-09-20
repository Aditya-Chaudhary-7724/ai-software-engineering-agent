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


def _network_available() -> bool:
    try:
        response = requests_lib.get("https://api.github.com", timeout=5)
        return response.status_code < 500
    except requests_lib.RequestException:
        return False


requires_network = pytest.mark.skipif(
    not _network_available(), reason="api.github.com not reachable (REQUIRES EXTERNAL SERVICE)"
)
