"""PostgreSQL-backed job queue (raw SQL via psycopg, like VectorStore).

Concurrency guarantees come from the database, not application checks:
- one active job per target: partial unique index + INSERT ... ON CONFLICT
  DO NOTHING (schema.sql), so two simultaneous submissions cannot both
  create a job;
- one worker per job: `claim_next` uses FOR UPDATE SKIP LOCKED, so any
  number of worker threads/processes can poll the same table safely;
- interrupted jobs: a running job's worker refreshes `heartbeat_at`; a job
  whose heartbeat goes stale (the process died or was killed) is failed
  with `worker_interrupted` instead of staying "running" forever.
"""

import uuid
from typing import Any, Dict, List, Optional, Tuple

import psycopg
from psycopg.types.json import Jsonb

from jobs.models import (
    GRAPH_NOT_REQUESTED,
    GRAPH_PENDING,
    STAGE_DONE,
    STAGE_QUEUED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    Job,
)

_CONNECT_TIMEOUT_SECONDS = 5
_COLUMNS = (
    "id, job_type, status, stage, target_path, github_full_name, repository_id, build_graph, graph_status, "
    "progress, error_code, created_at, started_at, finished_at, heartbeat_at"
)


def _row_to_job(row: tuple) -> Job:
    return Job(
        id=str(row[0]),
        job_type=row[1],
        status=row[2],
        stage=row[3],
        target_path=row[4],
        github_full_name=row[5],
        repository_id=row[6],
        build_graph=row[7],
        graph_status=row[8],
        progress=dict(row[9] or {}),
        error_code=row[10],
        created_at=row[11],
        started_at=row[12],
        finished_at=row[13],
        heartbeat_at=row[14],
    )


class JobStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._database_url, connect_timeout=_CONNECT_TIMEOUT_SECONDS)

    def _one(self, sql: str, params: Dict[str, Any]) -> Optional[Job]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
            conn.commit()
            return _row_to_job(row) if row else None
        finally:
            conn.close()

    def create_or_get_active(
        self,
        *,
        job_type: str,
        target_path: str,
        github_full_name: Optional[str],
        repository_id: Optional[int],
        build_graph: bool,
    ) -> Tuple[Job, bool]:
        """Returns `(job, created)`. If a queued/running job already exists
        for `target_path`, that job is returned with `created=False`."""
        params = {
            "id": str(uuid.uuid4()),
            "job_type": job_type,
            "target_path": target_path,
            "github_full_name": github_full_name,
            "repository_id": repository_id,
            "build_graph": build_graph,
            "graph_status": GRAPH_PENDING if build_graph else GRAPH_NOT_REQUESTED,
            "stage": STAGE_QUEUED,
            "status": STATUS_QUEUED,
        }
        # Two attempts: the existing active job can finish between our
        # failed insert and the lookup, in which case inserting again wins.
        for _ in range(2):
            created = self._one(
                f"""
                INSERT INTO jobs (id, job_type, status, stage, target_path, github_full_name, repository_id,
                                  build_graph, graph_status)
                VALUES (%(id)s, %(job_type)s, %(status)s, %(stage)s, %(target_path)s, %(github_full_name)s,
                        %(repository_id)s, %(build_graph)s, %(graph_status)s)
                ON CONFLICT (target_path) WHERE status IN ('queued', 'running') DO NOTHING
                RETURNING {_COLUMNS}
                """,
                params,
            )
            if created is not None:
                return created, True
            existing = self._one(
                f"SELECT {_COLUMNS} FROM jobs WHERE target_path = %(target_path)s AND status IN ('queued', 'running')",
                params,
            )
            if existing is not None:
                return existing, False
        raise RuntimeError("could not create or find an active job")  # pragma: no cover - needs a pathological race

    def get(self, job_id: str) -> Optional[Job]:
        return self._one(f"SELECT {_COLUMNS} FROM jobs WHERE id = %(id)s", {"id": job_id})

    def list_recent(self, limit: int, repository_id: Optional[int] = None, active_only: bool = False) -> List[Job]:
        conditions, params = [], {"limit": limit}
        if repository_id is not None:
            conditions.append("repository_id = %(repository_id)s")
            params["repository_id"] = repository_id
        if active_only:
            conditions.append("status IN ('queued', 'running')")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {_COLUMNS} FROM jobs {where} ORDER BY created_at DESC LIMIT %(limit)s", params)
                return [_row_to_job(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def claim_next(self, worker_id: str) -> Optional[Job]:
        return self._one(
            f"""
            UPDATE jobs SET status = %(running)s, worker_id = %(worker_id)s, started_at = now(), heartbeat_at = now()
            WHERE id = (
                SELECT id FROM jobs WHERE status = %(queued)s ORDER BY created_at
                FOR UPDATE SKIP LOCKED LIMIT 1
            )
            RETURNING {_COLUMNS}
            """,
            {"running": STATUS_RUNNING, "queued": STATUS_QUEUED, "worker_id": worker_id},
        )

    def update_stage(self, job_id: str, stage: str, progress: Optional[Dict[str, Any]] = None, **fields: Any) -> None:
        allowed = {"github_full_name", "graph_status", "repository_id"}
        if not set(fields) <= allowed:
            raise ValueError(f"unexpected job fields: {sorted(set(fields) - allowed)}")
        assignments = ", ".join(f"{name} = %({name})s" for name in fields)
        self._one(
            f"""
            UPDATE jobs SET stage = %(stage)s, heartbeat_at = now(),
                progress = progress || %(progress)s {', ' + assignments if assignments else ''}
            WHERE id = %(id)s AND status = 'running'
            RETURNING {_COLUMNS}
            """,
            {"id": job_id, "stage": stage, "progress": Jsonb(progress or {}), **fields},
        )

    def heartbeat(self, worker_id: str) -> None:
        self._one(
            f"UPDATE jobs SET heartbeat_at = now() WHERE worker_id = %(worker_id)s AND status = 'running' RETURNING {_COLUMNS}",
            {"worker_id": worker_id},
        )

    def complete(self, job_id: str, *, repository_id: int, graph_status: str, progress: Dict[str, Any]) -> None:
        self._one(
            f"""
            UPDATE jobs SET status = %(status)s, stage = %(stage)s, repository_id = %(repository_id)s,
                graph_status = %(graph_status)s, progress = progress || %(progress)s, finished_at = now()
            WHERE id = %(id)s AND status = 'running'
            RETURNING {_COLUMNS}
            """,
            {
                "id": job_id,
                "status": STATUS_COMPLETED,
                "stage": STAGE_DONE,
                "repository_id": repository_id,
                "graph_status": graph_status,
                "progress": Jsonb(progress),
            },
        )

    def fail(self, job_id: str, error_code: str, graph_status: Optional[str] = None) -> None:
        self._one(
            f"""
            UPDATE jobs SET status = %(status)s, error_code = %(error_code)s, finished_at = now(),
                graph_status = COALESCE(%(graph_status)s, CASE WHEN graph_status = 'pending' THEN 'not_run' ELSE graph_status END)
            WHERE id = %(id)s AND status IN ('queued', 'running')
            RETURNING {_COLUMNS}
            """,
            {"id": job_id, "status": STATUS_FAILED, "error_code": error_code, "graph_status": graph_status},
        )

    def fail_stale_running(self, stale_after_seconds: int) -> int:
        """Fails running jobs whose worker stopped heartbeating (crash, kill,
        restart). Returns how many were failed."""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs SET status = 'failed', error_code = 'worker_interrupted', finished_at = now(),
                        graph_status = CASE WHEN graph_status = 'pending' THEN 'not_run' ELSE graph_status END
                    WHERE status = 'running' AND heartbeat_at < now() - make_interval(secs => %(secs)s)
                    """,
                    {"secs": stale_after_seconds},
                )
                count = cur.rowcount
            conn.commit()
            return count
        finally:
            conn.close()

