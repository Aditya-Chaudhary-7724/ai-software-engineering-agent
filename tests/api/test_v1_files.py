"""File tree / file content endpoints: path safety and sensitive-file
protection, against a real repository on disk (real symlinks) and the
real test PostgreSQL."""

import pytest

from api.v1 import API_V1_PREFIX

from tests.api.conftest import auth_headers, requires_postgres

pytestmark = requires_postgres

_SECRETS = ("hunter2-should-never-leak", "sk-should-never-leak", "ghp_secret", "BEGIN PRIVATE KEY", "outside the repository root")


def _content(api_client, repository_id, path):
    return api_client.get(
        f"{API_V1_PREFIX}/repositories/{repository_id}/files/content", params={"path": path}, headers=auth_headers()
    )


def _assert_safe_body(response, root_path):
    assert root_path not in response.text
    for secret in _SECRETS:
        assert secret not in response.text


def test_file_tree_lists_source_and_excludes_sensitive_ignored_and_binary(api_client, registered_repository):
    repository_id, root_path = registered_repository

    response = api_client.get(f"{API_V1_PREFIX}/repositories/{repository_id}/files", headers=auth_headers())

    assert response.status_code == 200
    paths = [f["relative_path"] for f in response.json()["files"]]
    assert "src/app.py" in paths
    assert "src/util.js" in paths
    for excluded in (".env", "config/.env.production", "deploy.pem", ".git/config", "node_modules/pkg/index.js", "big.py", "image.dat", "escape_link.txt"):
        assert excluded not in paths
    assert response.json()["truncated"] is False
    _assert_safe_body(response, root_path)


def test_file_tree_directory_filter(api_client, registered_repository):
    repository_id, _ = registered_repository
    response = api_client.get(
        f"{API_V1_PREFIX}/repositories/{repository_id}/files", params={"directory": "src/"}, headers=auth_headers()
    )
    assert response.status_code == 200
    assert response.json()["directory"] == "src"
    assert {f["relative_path"] for f in response.json()["files"]} == {"src/app.py", "src/util.js"}


@pytest.mark.parametrize("directory", ["../", "..", "src/../../", "/etc"])
def test_file_tree_rejects_directory_traversal(api_client, registered_repository, directory):
    repository_id, root_path = registered_repository
    response = api_client.get(
        f"{API_V1_PREFIX}/repositories/{repository_id}/files", params={"directory": directory}, headers=auth_headers()
    )
    # "/etc" is stripped to "etc" (a relative name inside the root, which
    # simply doesn't exist), so it is safe either way: refused, or empty.
    if response.status_code == 200:
        assert response.json()["files"] == []
    else:
        assert response.status_code == 400
        assert response.json() == {"error": "path_not_allowed"}
    _assert_safe_body(response, root_path)


def test_reads_an_ordinary_source_file(api_client, registered_repository):
    repository_id, root_path = registered_repository

    response = _content(api_client, repository_id, "src/app.py")

    assert response.status_code == 200
    body = response.json()
    assert body["relative_path"] == "src/app.py"
    assert body["language"] == "Python"
    assert body["truncated"] is False
    assert "def greet" in body["content"]
    assert root_path not in response.text


def test_path_is_normalized_inside_the_root(api_client, registered_repository):
    repository_id, _ = registered_repository
    response = _content(api_client, repository_id, "src/../src/./app.py")
    assert response.status_code == 200
    assert response.json()["relative_path"] == "src/app.py"


@pytest.mark.parametrize(
    "path",
    ["../outside-secret.txt", "../../etc/passwd", "/etc/passwd", "src/../../outside-secret.txt", "escape_link.txt", "a\x00b"],
)
def test_traversal_absolute_symlink_and_nul_paths_are_refused(api_client, registered_repository, path):
    repository_id, root_path = registered_repository
    response = _content(api_client, repository_id, path)
    assert response.status_code == 400
    assert response.json() == {"error": "path_not_allowed"}
    _assert_safe_body(response, root_path)


@pytest.mark.parametrize(
    "path",
    [".env", "config/.env.production", "deploy.pem", ".git/config", "node_modules/pkg/index.js", ".ENV", "DEPLOY.PEM", ".GIT/config"],
)
def test_sensitive_and_ignored_directory_files_are_refused(api_client, registered_repository, path):
    """Upper-case variants matter on macOS's case-insensitive filesystem,
    where `.ENV` opens the same file as `.env`."""
    repository_id, root_path = registered_repository
    response = _content(api_client, repository_id, path)
    assert response.status_code == 403
    assert response.json() == {"error": "file_access_denied"}
    _assert_safe_body(response, root_path)


def test_a_nonexistent_sensitive_file_is_indistinguishable_from_an_existing_one(api_client, registered_repository):
    repository_id, _ = registered_repository
    assert _content(api_client, repository_id, "nested/.env.local").json() == {"error": "file_access_denied"}


def test_missing_oversized_and_binary_files(api_client, registered_repository):
    repository_id, root_path = registered_repository

    missing = _content(api_client, repository_id, "src/nope.py")
    oversized = _content(api_client, repository_id, "big.py")
    binary = _content(api_client, repository_id, "image.dat")
    directory = _content(api_client, repository_id, "src")

    assert (missing.status_code, missing.json()) == (404, {"error": "file_not_found"})
    assert (oversized.status_code, oversized.json()) == (413, {"error": "file_too_large"})
    assert (binary.status_code, binary.json()) == (415, {"error": "file_not_text"})
    assert (directory.status_code, directory.json()) == (404, {"error": "file_not_found"})
    for response in (missing, oversized, binary, directory):
        assert root_path not in response.text


def test_path_parameter_is_required_and_bounded(api_client, registered_repository):
    repository_id, _ = registered_repository
    base = f"{API_V1_PREFIX}/repositories/{repository_id}/files/content"
    assert api_client.get(base, headers=auth_headers()).json() == {"error": "invalid_request", "fields": ["query.path"]}
    assert api_client.get(base, params={"path": "a" * 2000}, headers=auth_headers()).status_code == 422


def test_file_tree_never_lists_case_variant_sensitive_files(api_client, registered_repository, sample_repository):
    """`.env` already exists in the fixture (and `.ENV` would collide with
    it on a case-insensitive filesystem), so use other variants."""
    repository_id, _ = registered_repository
    for name in ("ID_RSA", "Server.PEM", "Credentials.JSON", "PRIVATE.KEY"):
        (sample_repository / name).write_text("SECRET=casevariant\n")

    response = api_client.get(f"{API_V1_PREFIX}/repositories/{repository_id}/files", headers=auth_headers())

    paths = {f["relative_path"] for f in response.json()["files"]}
    assert paths.isdisjoint({"ID_RSA", "Server.PEM", "Credentials.JSON", "PRIVATE.KEY"})
    assert "casevariant" not in response.text
