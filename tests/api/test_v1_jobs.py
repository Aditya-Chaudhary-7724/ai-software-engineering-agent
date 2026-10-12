"""Ingestion/re-index job endpoints (frontend phase F3), through the real
FastAPI app and the real test PostgreSQL.

The worker is either a recording stub (`app.state.ingestion_worker`) for
submission tests, or a REAL IngestionWorker thread for the end-to-end test.
"""

import time
import uuid

import psycopg
import pytest

from api.v1 import API_V1_PREFIX
from jobs.worker import IngestionWorker

from tests.api.conftest import TEST_DATABASE_URL, auth_headers, insert_repository, requires_postgres
from tests.jobs.conftest import FakeGitHubService, make_executor

pytestmark = requires_postgres

JOBS = f"{API_V1_PREFIX}/ingestion-jobs"


class RecordingWorker:
    is_running = True

    def __init__(self):
        self.notifications = 0

    def notify(self):
        self.notifications += 1


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def job_api(app, api_client, monkeypatch, vector_store, workspace):  # noqa: F811
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    worker = RecordingWorker()
    app.state.ingestion_worker = worker
    return api_client, worker


def _submit(client, url, **extra):
    return client.post(JOBS, json={"github_url": url, **extra}, headers=auth_headers())


def _job_rows():
    conn = psycopg.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM jobs")
            return cur.fetchone()[0]
    finally:
        conn.close()


def test_submit_creates_a_queued_job_without_exposing_server_paths(job_api, workspace):
    client, worker = job_api

    response = _submit(client, "https://github.com/Octocat/Hello-World.git")

    assert response.status_code == 202
    body = response.json()
    assert body["created"] is True
    job = body["job"]
    assert (job["status"], job["stage"], job["job_type"]) == ("queued", "queued", "ingest_github")
    assert job["github_full_name"] == "octocat/hello-world"
    assert job["build_graph"] is False and job["graph_status"] == "not_requested"
    assert "target_path" not in job and str(workspace) not in response.text
    assert worker.notifications == 1
    assert response.headers["cache-control"] == "no-store"


def test_duplicate_submission_returns_the_running_job(job_api):
    client, worker = job_api
    first = _submit(client, "https://github.com/octocat/hello-world").json()["job"]

    again = _submit(client, "https://github.com/OCTOCAT/Hello-World/")

    assert again.status_code == 200
    assert again.json() == {"job": first, "created": False}
    assert worker.notifications == 1
    assert _job_rows() == 1


HOSTILE_URLS = [
    "/etc/passwd",
    "../../../etc/passwd",
    "file:///etc/passwd",
    "http://github.com/octocat/hello-world",
    "ssh://git@github.com/octocat/hello-world",
    "git@github.com:octocat/hello-world.git",
    "https://github.com.evil.example/octocat/hello-world",
    "https://evil.example/github.com/octocat/hello-world",
    "https://user:token@github.com/octocat/hello-world",
    "https://github.com@evil.example/octocat/hello-world",
    "https://github.com:8443/octocat/hello-world",
    "https://github.com/octocat",
    "https://github.com/octocat/hello-world/tree/main",
    "https://github.com/../../etc/passwd",
    "https://github.com/octocat/..",
    "https://github.com/octocat/hello-world;rm -rf",
    "https://github.com/octocat/hello\nworld",
    "https://github.com/octo%2F..%2Fcat/hello-world",
    "javascript:alert(1)",
    "",
    " ",
]


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_hostile_or_malformed_urls_are_rejected_without_creating_a_job(job_api, url, workspace):
    client, worker = job_api

    response = _submit(client, url)

    assert response.status_code == 422
    assert response.json()["error"] in ("invalid_github_url", "invalid_request")
    assert "evil" not in response.text and "token" not in response.text and "passwd" not in response.text
    assert _job_rows() == 0 and worker.notifications == 0
    assert list(workspace.iterdir()) == []


@pytest.mark.parametrize(
    "body",
    [
        {"github_url": "https://github.com/a/b", "local_path": "/etc"},
        {"github_url": "https://github.com/a/b", "target_path": "/tmp/x"},
        {"github_url": "https://github.com/a/b", "workspace_root": "/"},
        {"github_url": "https://github.com/a/b", "build_graph": "true"},
        {"github_url": "https://github.com/a/b", "build_graph": 1},
        {"github_url": ["https://github.com/a/b"]},
        {"github_url": "https://github.com/a/" + "b" * 400},
        {},
    ],
)
def test_bodies_with_paths_extra_fields_or_wrong_types_are_rejected(job_api, body):
    client, _ = job_api
    response = client.post(JOBS, json=body, headers=auth_headers())
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert "/etc" not in response.text and "/tmp" not in response.text
    assert _job_rows() == 0


def test_graph_request_without_neo4j_is_refused(job_api):
    client, _ = job_api
    response = _submit(client, "https://github.com/octocat/hello-world", build_graph=True)
    assert (response.status_code, response.json()) == (409, {"error": "graph_not_configured"})


def test_already_ingested_repository_points_to_the_existing_record(job_api, vector_store, workspace):
    client, _ = job_api
    repository_id = insert_repository(vector_store, str((workspace / "octocat" / "hello-world").resolve()), name="hello-world")
    (workspace / "octocat" / "hello-world").mkdir(parents=True)

    response = _submit(client, "https://github.com/octocat/Hello-World")

    assert response.status_code == 409
    assert response.json() == {"error": "repository_already_ingested", "repository_id": repository_id}


def test_ingestion_is_refused_when_not_configured_or_worker_down(app, job_api, monkeypatch):
    client, _ = job_api
    monkeypatch.setenv("WORKSPACE_ROOT", "relative/workspace")
    assert _submit(client, "https://github.com/a/b").json() == {"error": "ingestion_not_configured"}

    monkeypatch.delenv("WORKSPACE_ROOT")
    assert _submit(client, "https://github.com/a/b").status_code == 503

    monkeypatch.setenv("WORKSPACE_ROOT", "/tmp/some-workspace")
    app.state.ingestion_worker = None
    response = _submit(client, "https://github.com/a/b")
    assert (response.status_code, response.json()) == (503, {"error": "job_worker_unavailable"})
    assert _job_rows() == 0


def test_reindex_enqueues_once_and_validates_the_repository(job_api, registered_repository):
    client, worker = job_api
    repository_id, root_path = registered_repository

    first = client.post(f"{API_V1_PREFIX}/repositories/{repository_id}/reindex", json={}, headers=auth_headers())
    again = client.post(f"{API_V1_PREFIX}/repositories/{repository_id}/reindex", json={"build_graph": False}, headers=auth_headers())
    missing = client.post(f"{API_V1_PREFIX}/repositories/999999/reindex", json={}, headers=auth_headers())
    with_path = client.post(f"{API_V1_PREFIX}/repositories/{repository_id}/reindex", json={"root_path": "/"}, headers=auth_headers())

    assert first.status_code == 202 and first.json()["job"]["job_type"] == "reindex"
    assert first.json()["job"]["repository_id"] == repository_id
    assert again.status_code == 200 and again.json()["job"]["id"] == first.json()["job"]["id"]
    assert missing.status_code == 404
    assert with_path.status_code == 422
    assert root_path not in first.text
    assert worker.notifications == 1


def test_reindex_of_a_repository_whose_files_are_gone(job_api, registered_repository, sample_repository):
    import shutil

    client, _ = job_api
    repository_id, _ = registered_repository
    shutil.rmtree(sample_repository)
    response = client.post(f"{API_V1_PREFIX}/repositories/{repository_id}/reindex", json={}, headers=auth_headers())
    assert (response.status_code, response.json()) == (409, {"error": "repository_files_unavailable"})


def test_job_status_lookup_validation(job_api):
    client, _ = job_api
    for bad in ("not-a-uuid", str(uuid.uuid4()).upper(), "{%s}" % uuid.uuid4(), "%s.json" % uuid.uuid4()):
        assert client.get(f"{JOBS}/{bad}", headers=auth_headers()).json() == {"error": "invalid_job_id"}
    # Dot segments are normalized away and encoded slashes split into extra
    # segments: no job route matches at all.
    for bad in ("..", "..%2F..%2Fetc"):
        assert client.get(f"{JOBS}/{bad}", headers=auth_headers()).status_code in (200, 404)
    assert client.get(f"{JOBS}/{uuid.uuid4()}", headers=auth_headers()).json() == {"error": "job_not_found"}


def test_status_only_returns_allowlisted_progress(job_api):
    client, _ = job_api
    job = _submit(client, "https://github.com/octocat/hello-world").json()["job"]
    conn = psycopg.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET progress = %s::jsonb WHERE id = %s",
            ('{"chunks_indexed": 3, "internal_path": "/srv/secret", "stderr": "fatal: token"}', job["id"]),
        )
    conn.commit()
    conn.close()

    body = client.get(f"{JOBS}/{job['id']}", headers=auth_headers()).json()

    assert body["progress"] == {"chunks_indexed": 3}


def test_list_filters_by_repository_and_activity(job_api, registered_repository):
    client, _ = job_api
    repository_id, _ = registered_repository
    _submit(client, "https://github.com/octocat/hello-world")
    client.post(f"{API_V1_PREFIX}/repositories/{repository_id}/reindex", json={}, headers=auth_headers())

    everything = client.get(JOBS, headers=auth_headers()).json()["jobs"]
    for_repo = client.get(JOBS, params={"repository_id": repository_id, "active": True}, headers=auth_headers()).json()["jobs"]

    assert len(everything) == 2
    assert [j["job_type"] for j in for_repo] == ["reindex"]


def test_end_to_end_submit_then_poll_with_a_real_worker_thread(app, job_api, workspace):
    """Real worker thread + real indexing; only GitHub is faked."""
    from jobs.store import JobStore

    client, _ = job_api
    store = JobStore(TEST_DATABASE_URL)
    worker = IngestionWorker(store, make_executor(store, workspace, github=FakeGitHubService()), poll_interval_seconds=0.05)
    app.state.ingestion_worker = worker
    worker.start()
    try:
        job = _submit(client, "https://github.com/octo/demo").json()["job"]
        seen_statuses = []
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            polled = client.get(f"{JOBS}/{job['id']}", headers=auth_headers()).json()
            seen_statuses.append(polled["status"])
            if polled["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
    finally:
        assert worker.stop(timeout_seconds=10)

    assert polled["status"] == "completed", polled
    assert polled["progress"]["chunks_indexed"] == 2
    repository = client.get(f"{API_V1_PREFIX}/repositories/{polled['repository_id']}", headers=auth_headers()).json()
    assert repository["github_full_name"] == "octo/demo"
    files = client.get(f"{API_V1_PREFIX}/repositories/{polled['repository_id']}/files", headers=auth_headers()).json()
    assert {f["relative_path"] for f in files["files"]} == {"src/app.py", "README.md"}
