"""JobStore against the real test PostgreSQL: de-duplication, claiming,
terminal-state transitions, and interrupted-job detection."""

import threading

import psycopg

from tests.jobs.conftest import TEST_DATABASE_URL, queue_ingest, requires_postgres

pytestmark = requires_postgres


def test_duplicate_submission_returns_the_active_job(job_store, workspace):
    first, _ = queue_ingest(job_store, workspace)
    again, created = job_store.create_or_get_active(
        job_type="ingest_github", target_path=first.target_path, github_full_name="octo/demo",
        repository_id=None, build_graph=False,
    )
    assert created is False
    assert again.id == first.id
    assert first.status == "queued" and first.stage == "queued" and first.graph_status == "not_requested"


def test_concurrent_identical_submissions_create_exactly_one_job(job_store, workspace):
    target = str(workspace / "octo" / "race")
    results, errors = [], []

    def submit():
        try:
            results.append(job_store.create_or_get_active(
                job_type="ingest_github", target_path=target, github_full_name="octo/race",
                repository_id=None, build_graph=False,
            ))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=submit) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    assert sum(1 for _, created in results if created) == 1
    assert len({job.id for job, _ in results}) == 1


def test_a_new_job_can_be_created_once_the_previous_one_finished(job_store, workspace):
    first, _ = queue_ingest(job_store, workspace)
    claimed = job_store.claim_next("w")
    job_store.fail(claimed.id, "clone_failed")
    second, created = job_store.create_or_get_active(
        job_type="ingest_github", target_path=first.target_path, github_full_name="octo/demo",
        repository_id=None, build_graph=False,
    )
    assert created is True and second.id != first.id


def test_concurrent_claimers_never_get_the_same_job(job_store, workspace):
    for i in range(5):
        queue_ingest(job_store, workspace, full_name=f"octo/r{i}")
    claimed, lock = [], threading.Lock()

    def claim():
        while True:
            job = job_store.claim_next(f"w-{threading.get_ident()}")
            if job is None:
                return
            with lock:
                claimed.append(job.id)

    threads = [threading.Thread(target=claim) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(claimed) == 5 and len(set(claimed)) == 5


def test_claims_oldest_first_and_records_the_worker(job_store, workspace):
    older, _ = queue_ingest(job_store, workspace, full_name="octo/older")
    queue_ingest(job_store, workspace, full_name="octo/newer")
    claimed = job_store.claim_next("worker-a")
    assert claimed.id == older.id
    assert claimed.status == "running" and claimed.started_at is not None


def test_terminal_states_are_final(job_store, workspace):
    job, _ = queue_ingest(job_store, workspace)
    job_store.claim_next("w")
    job_store.fail(job.id, "clone_failed")
    job_store.complete(job.id, repository_id=1, graph_status="not_requested", progress={})  # must be a no-op
    final = job_store.get(job.id)
    assert final.status == "failed" and final.error_code == "clone_failed" and final.finished_at is not None


def test_stale_running_jobs_are_failed_as_interrupted_but_live_ones_are_not(job_store, workspace):
    stale, _ = queue_ingest(job_store, workspace, full_name="octo/stale")
    live, _ = queue_ingest(job_store, workspace, full_name="octo/live", build_graph=True)
    job_store.claim_next("dead-worker")
    job_store.claim_next("live-worker")
    conn = psycopg.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET heartbeat_at = now() - interval '10 minutes' WHERE id = %s", (stale.id,))
    conn.commit()
    conn.close()

    assert job_store.fail_stale_running(120) == 1
    assert job_store.get(stale.id).error_code == "worker_interrupted"
    assert job_store.get(live.id).status == "running"


def test_interrupted_job_with_requested_graph_reports_graph_not_run(job_store, workspace):
    job, _ = queue_ingest(job_store, workspace, build_graph=True)
    assert job.graph_status == "pending"
    job_store.claim_next("w")
    job_store.fail(job.id, "worker_interrupted")
    assert job_store.get(job.id).graph_status == "not_run"
