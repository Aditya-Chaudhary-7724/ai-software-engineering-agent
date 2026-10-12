"""IngestionExecutor: real cloning-destination logic, real ingestion,
parsing, chunking, embedding and PostgreSQL indexing. Only the GitHub
service is faked (tests.jobs.conftest.FakeGitHubService)."""

import psycopg
import pytest

from github_integration.exceptions import GitHubAPIError, GitOperationError
from graph.exceptions import MissingCredentialsError

from tests.jobs.conftest import (
    SECRET_CONTENT,
    TEST_DATABASE_URL,
    FakeGitHubService,
    make_executor,
    queue_ingest,
    requires_postgres,
    run_one,
)

pytestmark = requires_postgres


def _db(sql, params=()):
    conn = psycopg.connect(TEST_DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def test_successful_ingestion_indexes_source_and_excludes_sensitive_files(job_store, workspace):
    job, destination = queue_ingest(job_store, workspace)

    final = run_one(job_store, make_executor(job_store, workspace))

    assert final.status == "completed" and final.stage == "done" and final.error_code is None
    assert final.graph_status == "not_requested"
    assert final.progress["chunks_indexed"] == 2
    assert final.progress["embedding_provider"] == "deterministic_local"
    [(root_path,)] = _db("SELECT root_path FROM repositories WHERE id = %s", (final.repository_id,))
    assert root_path == str(destination)
    paths = {p for (p,) in _db("SELECT DISTINCT relative_path FROM code_chunks WHERE repository_id = %s", (final.repository_id,))}
    assert paths == {"src/app.py"}  # no .ENV / ID_RSA / Server.PEM / Credentials.JSON / node_modules
    assert _db("SELECT count(*) FROM code_chunks WHERE content LIKE %s", (f"%{SECRET_CONTENT}%",)) == [(0,)]


def test_github_names_are_case_normalized_to_one_checkout_and_one_dedupe_key():
    from jobs.ingestion import canonical_github_reference

    for url in ("https://github.com/Octo/Demo", "https://github.com/OCTO/demo.git", "https://github.com/octo/DEMO/"):
        reference = canonical_github_reference(url)
        assert (reference.owner, reference.repo, reference.url) == ("octo", "demo", "https://github.com/octo/demo")


@pytest.mark.parametrize(
    "github, code",
    [
        (FakeGitHubService(size_kb=10_000_000), "repository_too_large"),
        (FakeGitHubService(metadata_error=GitHubAPIError("Repository 'octo/demo' was not found (or is private...)")), "github_repository_not_found"),
        (FakeGitHubService(metadata_error=GitHubAPIError("GitHub API error fetching 'octo/demo': 403 rate limit")), "github_rate_limited"),
        (FakeGitHubService(clone_error=GitOperationError("git clone failed: fatal: https://x-access-token:ghp_SECRET@github.com/...")), "clone_failed"),
    ],
)
def test_github_failures_become_safe_codes_and_leave_no_checkout(job_store, workspace, github, code):
    job, destination = queue_ingest(job_store, workspace)

    final = run_one(job_store, make_executor(job_store, workspace, github=github))

    assert final.status == "failed" and final.error_code == code
    assert not destination.exists()  # including a partially written clone
    # Nothing from the exception message is stored anywhere on the job.
    [(row,)] = _db("SELECT row_to_json(j)::text FROM jobs j WHERE id = %s", (job.id,))
    assert "ghp_SECRET" not in row and "x-access-token" not in row and "rate limit" not in row


def test_a_leftover_checkout_from_an_interrupted_job_is_replaced_on_retry(job_store, workspace):
    """A killed worker can't clean up: its partial checkout stays on disk
    with no repository row. The next ingestion of that repository removes
    it and proceeds."""
    leftover = workspace / "octo" / "demo"
    (leftover / "half-written").mkdir(parents=True)
    queue_ingest(job_store, workspace)
    final = run_one(job_store, make_executor(job_store, workspace))
    assert final.status == "completed"


def test_indexing_failure_removes_the_fresh_checkout_and_writes_no_repository(job_store, workspace):
    job, destination = queue_ingest(job_store, workspace)

    def broken_provider():
        class Broken:
            def embed(self, texts):
                raise RuntimeError("embedding service exploded at /srv/secret/path")

        return Broken(), "broken"

    executor = make_executor(job_store, workspace)
    executor._deps.embedding_provider_factory = broken_provider
    final = run_one(job_store, executor)

    assert final.status == "failed" and final.error_code == "indexing_failed"
    assert not destination.exists()
    assert _db("SELECT count(*) FROM repositories") == [(0,)]


def test_an_already_ingested_repository_is_not_cloned_again(job_store, workspace):
    queue_ingest(job_store, workspace)
    run_one(job_store, make_executor(job_store, workspace))
    github = FakeGitHubService()

    queue_ingest(job_store, workspace)
    final = run_one(job_store, make_executor(job_store, workspace, github=github))

    assert final.error_code == "repository_already_ingested"
    assert github.clone_calls == 0


def test_a_tampered_target_path_is_refused_and_nothing_is_written(job_store, workspace, tmp_path):
    outside = tmp_path / "outside"
    job, _ = job_store.create_or_get_active(
        job_type="ingest_github", target_path=str(outside), github_full_name="octo/demo",
        repository_id=None, build_graph=False,
    )
    github = FakeGitHubService()

    final = run_one(job_store, make_executor(job_store, workspace, github=github))

    assert final.error_code == "workspace_error"
    assert github.clone_calls == 0 and not outside.exists()


def test_reindex_replaces_chunks_without_duplicates(job_store, workspace):
    queue_ingest(job_store, workspace)
    first = run_one(job_store, make_executor(job_store, workspace))
    destination = workspace / "octo" / "demo"
    (destination / "src" / "app.py").write_text("def only_this():\n    return 1\n")

    job_store.create_or_get_active(
        job_type="reindex", target_path=str(destination), github_full_name="octo/demo",
        repository_id=first.repository_id, build_graph=False,
    )
    final = run_one(job_store, make_executor(job_store, workspace))

    assert final.status == "completed" and final.repository_id == first.repository_id
    rows = _db("SELECT qualified_name FROM code_chunks WHERE repository_id = %s", (first.repository_id,))
    assert rows == [("only_this",)]


def test_reindex_of_missing_files_fails_safely(job_store, workspace):
    job_store.create_or_get_active(
        job_type="reindex", target_path=str(workspace / "gone"), github_full_name=None,
        repository_id=None, build_graph=False,
    )
    final = run_one(job_store, make_executor(job_store, workspace))
    assert final.status == "failed" and final.error_code == "repository_files_unavailable"


def test_graph_requested_but_not_configured_completes_the_index_and_reports_it(job_store, workspace):
    queue_ingest(job_store, workspace, build_graph=True)

    def missing():
        raise MissingCredentialsError("NEO4J_URI ... must all be set")

    final = run_one(job_store, make_executor(job_store, workspace, neo4j_factory=missing))

    assert final.status == "completed" and final.progress["chunks_indexed"] == 2
    assert final.graph_status == "failed" and final.progress["graph_error_code"] == "graph_not_configured"


def test_graph_build_failure_is_reported_without_discarding_the_index(job_store, workspace):
    queue_ingest(job_store, workspace, build_graph=True)

    class ExplodingClient:
        def run(self, *args, **kwargs):
            raise RuntimeError("bolt://neo4j:password@10.0.0.5 unavailable")

        def close(self):
            pass

    final = run_one(job_store, make_executor(job_store, workspace, neo4j_factory=ExplodingClient))

    assert final.status == "completed" and final.repository_id is not None
    assert final.graph_status == "failed" and final.progress["graph_error_code"] == "graph_build_failed"
    [(row,)] = _db("SELECT row_to_json(j)::text FROM jobs j WHERE id = %s", (final.id,))
    assert "password" not in row and "10.0.0.5" not in row


def test_failure_before_graph_step_marks_requested_graph_not_run(job_store, workspace):
    queue_ingest(job_store, workspace, build_graph=True)
    final = run_one(job_store, make_executor(job_store, workspace, github=FakeGitHubService(size_kb=10**9)))
    assert final.status == "failed" and final.graph_status == "not_run"


def test_unexpected_errors_become_internal_error(job_store, workspace):
    queue_ingest(job_store, workspace)

    class Crashing(FakeGitHubService):
        def get_repository_metadata(self, url):
            raise KeyError("unexpected shape from somewhere")

    final = run_one(job_store, make_executor(job_store, workspace, github=Crashing()))
    assert final.status == "failed" and final.error_code == "internal_error"
