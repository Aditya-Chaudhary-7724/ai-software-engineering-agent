"""REAL end-to-end ingestion: authenticated API -> real worker thread ->
real GitHubIntegrationService (api.github.com + `git clone`) -> real
ingestion/indexing into the test PostgreSQL.

Uses GitHub's own canonical public example repository (the same one the
Phase 11 tests use). Skips, never fails, without network access.
"""

import time

from fastapi.testclient import TestClient

from api.main import create_app
from api.v1 import API_V1_PREFIX
from jobs.ingestion import IngestionDependencies, IngestionExecutor
from jobs.store import JobStore
from jobs.worker import IngestionWorker

from tests.api.conftest import TEST_SERVICE_TOKEN, auth_headers
from tests.github_integration.conftest import PUBLIC_TEST_REPO_URL, requires_network
from tests.jobs.conftest import TEST_DATABASE_URL, requires_postgres


@requires_postgres
@requires_network
def test_real_github_repository_is_cloned_and_indexed_through_the_api(monkeypatch, vector_store, tmp_path):  # noqa: F811
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("API_SERVICE_TOKEN", TEST_SERVICE_TOKEN)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)  # public repository: no credentials involved

    app = create_app()
    store = JobStore(TEST_DATABASE_URL)
    worker = IngestionWorker(
        store,
        IngestionExecutor(store, IngestionDependencies(database_url=TEST_DATABASE_URL, workspace_root=str(workspace))),
        poll_interval_seconds=0.1,
    )
    app.state.ingestion_worker = worker
    worker.start()
    client = TestClient(app)
    try:
        submitted = client.post(f"{API_V1_PREFIX}/ingestion-jobs", json={"github_url": PUBLIC_TEST_REPO_URL}, headers=auth_headers())
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["job"]["id"]
        stages = []
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = client.get(f"{API_V1_PREFIX}/ingestion-jobs/{job_id}", headers=auth_headers()).json()
            if not stages or stages[-1] != job["stage"]:
                stages.append(job["stage"])
            if job["status"] in ("completed", "failed"):
                break
            time.sleep(0.2)
        again = client.post(f"{API_V1_PREFIX}/ingestion-jobs", json={"github_url": PUBLIC_TEST_REPO_URL}, headers=auth_headers())
    finally:
        assert worker.stop(timeout_seconds=30)

    assert job["status"] == "completed", job
    assert stages[-1] == "done"
    assert (workspace / "octocat" / "hello-world" / ".git").is_dir()
    repository = client.get(f"{API_V1_PREFIX}/repositories/{job['repository_id']}", headers=auth_headers()).json()
    assert repository["github_full_name"] == "octocat/hello-world"
    assert repository["available_on_disk"] is True
    # Hello-World holds only a README (not a source file), so it is ingested
    # but produces no code chunks — reported as measured, not padded.
    assert job["progress"]["chunks_indexed"] == repository["chunk_count"] == 0
    assert again.status_code == 409 and again.json()["repository_id"] == job["repository_id"]
