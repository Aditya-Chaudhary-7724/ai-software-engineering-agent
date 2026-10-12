"""IngestionWorker lifecycle with a real thread and the real database."""

import time

import psycopg

from jobs.worker import IngestionWorker

from tests.jobs.conftest import TEST_DATABASE_URL, make_executor, queue_ingest, requires_postgres

pytestmark = requires_postgres


def _wait_terminal(job_store, job_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = job_store.get(job_id)
        if job.status in ("completed", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_worker_thread_runs_a_job_and_stops_cleanly(job_store, workspace):
    worker = IngestionWorker(job_store, make_executor(job_store, workspace), poll_interval_seconds=0.05)
    worker.start()
    try:
        job, _ = queue_ingest(job_store, workspace)
        worker.notify()
        final = _wait_terminal(job_store, job.id)
    finally:
        stopped = worker.stop(timeout_seconds=10)

    assert final.status == "completed"
    assert stopped is True and worker.is_running is False


def test_worker_fails_stale_jobs_left_by_a_dead_worker(job_store, workspace):
    job, _ = queue_ingest(job_store, workspace)
    job_store.claim_next("worker-that-died")
    conn = psycopg.connect(TEST_DATABASE_URL)
    with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", (job.id,))
    conn.commit()
    conn.close()

    worker = IngestionWorker(job_store, make_executor(job_store, workspace), stale_after_seconds=120)
    assert worker.run_once() is False  # nothing queued; the stale job is recovered first

    final = job_store.get(job.id)
    assert final.status == "failed" and final.error_code == "worker_interrupted"


def test_heartbeat_keeps_a_long_job_alive(job_store, workspace):
    job, _ = queue_ingest(job_store, workspace)

    class SlowExecutor:
        def execute(self, claimed):
            time.sleep(0.6)
            job_store.complete(claimed.id, repository_id=None, graph_status="not_requested", progress={})

    worker = IngestionWorker(job_store, SlowExecutor(), heartbeat_interval_seconds=0.1)
    worker.run_once()
    final = job_store.get(job.id)
    assert final.heartbeat_at > final.started_at  # refreshed while the job ran


def test_worker_loop_survives_a_database_outage(workspace):
    from jobs.store import JobStore

    unreachable = JobStore("postgresql://nobody@127.0.0.1:1/nothing")
    worker = IngestionWorker(unreachable, executor=None, poll_interval_seconds=0.05)
    worker.start()
    time.sleep(0.3)
    assert worker.is_running  # still alive and retrying, not crashed
    assert worker.stop(timeout_seconds=5) is True
