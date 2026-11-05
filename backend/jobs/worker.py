"""In-process background worker (one daemon thread per API process).

Started and stopped by the FastAPI lifespan (api/main.py). Each loop:
1. fail jobs whose worker stopped heartbeating (crash/kill/restart);
2. claim the oldest queued job (FOR UPDATE SKIP LOCKED — safe with several
   API processes each running a worker);
3. execute it, refreshing its heartbeat from a side thread;
4. otherwise sleep until the poll interval passes or `notify()` is called
   (job submission wakes the worker immediately).

Shutdown: `stop()` lets the current job finish within the timeout. If it
can't, the daemon thread is abandoned with the process; the job's heartbeat
then goes stale and the next worker to start marks it `worker_interrupted`
rather than leaving it "running" forever.

F6: the loop is generic (`BackgroundWorker`) so agent tasks reuse it with
their own store/executor; `IngestionWorker` is the F3 configuration. Any
store must provide `claim_next(worker_id)` and `heartbeat(worker_id)`;
`recover(stale_after_seconds)` defaults to `store.fail_stale_running`.
"""

import logging
import threading
import time
import uuid
from typing import Any, Callable, Optional

import psycopg

logger = logging.getLogger("jobs.worker")


class BackgroundWorker:
    def __init__(
        self,
        store: Any,
        executor: Any,
        *,
        poll_interval_seconds: float = 2.0,
        heartbeat_interval_seconds: float = 15.0,
        stale_after_seconds: int = 120,
        worker_id: Optional[str] = None,
        name: str = "ingestion",
        recover: Optional[Callable[[int], object]] = None,
    ) -> None:
        self._store = store
        self._executor = executor
        self._name = name
        self._recover = recover
        self._poll_interval = poll_interval_seconds
        self._heartbeat_interval = heartbeat_interval_seconds
        self._stale_after = stale_after_seconds
        self.worker_id = worker_id or f"worker-{uuid.uuid4()}"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_error_log = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"{self._name}-{self.worker_id}", daemon=True)
        self._thread.start()
        logger.info("%s worker started", self._name, extra={"worker_id": self.worker_id})

    def notify(self) -> None:
        self._wake.set()

    def stop(self, timeout_seconds: float = 10.0) -> bool:
        """Returns True if the worker thread exited within the timeout."""
        self._stop.set()
        self._wake.set()
        if self._thread is None:
            return True
        self._thread.join(timeout_seconds)
        stopped = not self._thread.is_alive()
        logger.info("%s worker stopped" if stopped else "%s worker still busy at shutdown", self._name,
                    extra={"worker_id": self.worker_id})
        return stopped

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def run_once(self) -> bool:
        """One iteration (also used directly by tests). Returns True if a job ran."""
        (self._recover or self._store.fail_stale_running)(self._stale_after)
        job = self._store.claim_next(self.worker_id)
        if job is None:
            return False
        self._execute_with_heartbeat(job)
        return True

    def _execute_with_heartbeat(self, job) -> None:
        done = threading.Event()

        def beat() -> None:
            while not done.wait(self._heartbeat_interval):
                try:
                    self._store.heartbeat(self.worker_id)
                except Exception:
                    logger.warning("heartbeat failed", extra={"worker_id": self.worker_id})

        beater = threading.Thread(target=beat, name=f"heartbeat-{self.worker_id}", daemon=True)
        beater.start()
        try:
            self._executor.execute(job)
        finally:
            done.set()
            beater.join(self._heartbeat_interval)

    def _run(self) -> None:
        while not self._stop.is_set():
            ran = False
            try:
                ran = self.run_once()
            except (psycopg.OperationalError, psycopg.errors.UndefinedTable):
                # Database down or schema not applied: back off quietly and
                # retry; log at most once a minute.
                if time.monotonic() - self._last_error_log > 60:
                    logger.warning("%s worker cannot reach its jobs table; retrying", self._name)
                    self._last_error_log = time.monotonic()
            except Exception:
                logger.exception("%s worker loop error", self._name)
            if not ran:
                self._wake.wait(self._poll_interval)
                self._wake.clear()


class IngestionWorker(BackgroundWorker):
    """The F3 ingestion/re-index worker (unchanged behaviour)."""
