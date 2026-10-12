"""Repository list/detail endpoints against the real test PostgreSQL."""

import shutil

from api.v1 import API_V1_PREFIX

from tests.api.conftest import auth_headers, insert_repository, requires_postgres


def _assert_no_server_path(response, *paths) -> None:
    for path in paths:
        assert path not in response.text
    assert "root_path" not in response.text


@requires_postgres
def test_list_repositories_returns_counts_without_server_paths(api_client, registered_repository):
    repository_id, root_path = registered_repository

    response = api_client.get(f"{API_V1_PREFIX}/repositories", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    [repo] = body["repositories"]
    assert repo["id"] == repository_id
    assert repo["name"] == "sample-repo"
    assert repo["chunk_count"] == 3
    assert repo["indexed_file_count"] == 3
    assert repo["languages"] == ["javascript", "python"]
    assert repo["available_on_disk"] is True
    _assert_no_server_path(response, root_path)


@requires_postgres
def test_list_repositories_paginates(api_client, monkeypatch, vector_store, tmp_path):
    from tests.api.conftest import TEST_DATABASE_URL

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    for i in range(3):
        insert_repository(vector_store, str(tmp_path / f"repo-{i}"), name=f"repo-{i}")

    page = api_client.get(f"{API_V1_PREFIX}/repositories", params={"limit": 2, "offset": 2}, headers=auth_headers())

    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert len(page.json()["repositories"]) == 1


@requires_postgres
def test_repository_detail_includes_breakdowns(api_client, registered_repository):
    repository_id, root_path = registered_repository

    response = api_client.get(f"{API_V1_PREFIX}/repositories/{repository_id}", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["chunks_by_language"] == {"python": 2, "javascript": 1}
    assert body["chunks_by_type"] == {"function": 3}
    _assert_no_server_path(response, root_path)


@requires_postgres
def test_repository_whose_files_are_gone_is_reported_not_hidden(api_client, registered_repository, sample_repository):
    repository_id, _ = registered_repository
    shutil.rmtree(sample_repository)

    detail = api_client.get(f"{API_V1_PREFIX}/repositories/{repository_id}", headers=auth_headers())
    files = api_client.get(f"{API_V1_PREFIX}/repositories/{repository_id}/files", headers=auth_headers())

    assert detail.json()["available_on_disk"] is False
    assert files.status_code == 409
    assert files.json() == {"error": "repository_files_unavailable"}


@requires_postgres
def test_unknown_repository_is_404(api_client, registered_repository):
    response = api_client.get(f"{API_V1_PREFIX}/repositories/999999", headers=auth_headers())
    assert response.status_code == 404
    assert response.json() == {"error": "repository_not_found"}


def test_invalid_repository_ids_are_rejected_without_echoing_input(api_client, monkeypatch):
    # Never connected to: validation fails before any database access.
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@127.0.0.1:1/unused")
    for bad in ("abc", "0", "-1", "1.5", "9" * 30):
        response = api_client.get(f"{API_V1_PREFIX}/repositories/{bad}", headers=auth_headers())
        assert response.status_code == 422, bad
        assert response.json() == {"error": "invalid_request", "fields": ["path.repository_id"]}


def test_invalid_pagination_is_rejected(api_client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@127.0.0.1:1/unused")
    response = api_client.get(f"{API_V1_PREFIX}/repositories", params={"limit": 1000}, headers=auth_headers())
    assert response.status_code == 422
    assert response.json()["fields"] == ["query.limit"]


def test_database_not_configured_is_a_generic_503(api_client, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    response = api_client.get(f"{API_V1_PREFIX}/repositories", headers=auth_headers())
    assert response.status_code == 503
    assert response.json() == {"error": "database_not_configured"}


def test_unreachable_database_never_leaks_connection_details(api_client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://leaky_user:leaky_password@127.0.0.1:1/leaky_db")

    response = api_client.get(f"{API_V1_PREFIX}/repositories", headers=auth_headers())

    assert response.status_code == 503
    assert response.json() == {"error": "database_unavailable"}
    for fragment in ("leaky_user", "leaky_password", "leaky_db", "127.0.0.1"):
        assert fragment not in response.text
