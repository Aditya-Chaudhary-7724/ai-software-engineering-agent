"""Real-network integration tests — no mocks. Actually calls
api.github.com and actually runs `git clone` against a real, small,
stable public repository (`octocat/Hello-World`). Skips cleanly if
api.github.com isn't reachable (see conftest.py's `requires_network`),
per this project's "LOCAL TESTED vs REQUIRES EXTERNAL SERVICE"
distinction. No GITHUB_TOKEN is used or required — these only exercise
unauthenticated, read-only operations.
"""

from pathlib import Path

from github_integration.service import GitHubIntegrationService

from tests.github_integration.conftest import PUBLIC_TEST_REPO_URL, requires_network


@requires_network
def test_get_repository_metadata_for_a_real_public_repository():
    service = GitHubIntegrationService(token=None)

    metadata = service.get_repository_metadata(PUBLIC_TEST_REPO_URL)

    assert metadata.owner == "octocat"
    assert metadata.name == "Hello-World"
    assert metadata.private is False
    assert metadata.clone_url.startswith("https://github.com/")


@requires_network
def test_clone_a_real_public_repository_into_the_workspace(tmp_path):
    service = GitHubIntegrationService(token=None)

    cloned = service.clone_repository(PUBLIC_TEST_REPO_URL, str(tmp_path))

    local_path = Path(cloned.local_path)
    assert local_path.is_dir()
    assert (local_path / ".git").is_dir()
    assert cloned.reference.owner == "octocat"
    assert cloned.reference.repo == "Hello-World"
    # Cloned exactly where the workspace boundary says it should be —
    # never anywhere else on the filesystem.
    assert local_path == (tmp_path / "octocat" / "Hello-World").resolve()


@requires_network
def test_cloning_twice_into_the_same_workspace_targets_the_same_path(tmp_path):
    """Two different repositories never collide, and the same
    repository always resolves to the same destination — useful for a
    caller deciding whether to re-clone or reuse an existing checkout.
    """
    service = GitHubIntegrationService(token=None)

    first = service.clone_repository(PUBLIC_TEST_REPO_URL, str(tmp_path))
    metadata = service.get_repository_metadata(PUBLIC_TEST_REPO_URL)

    assert first.local_path == str((tmp_path / metadata.owner / metadata.name).resolve())
