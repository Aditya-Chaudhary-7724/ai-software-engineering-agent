"""PostgreSQL-backed agent-task queue and approval gate (raw SQL via
psycopg, the same approach as `jobs/store.py`).

Every guarantee below comes from a single SQL statement, not from a
check-then-act sequence in Python:

- one active task per repository: partial unique index + INSERT ... ON
  CONFLICT DO NOTHING (schema.sql);
- one worker per task: `claim_next` uses FOR UPDATE SKIP LOCKED;
- ONE-TIME, BOUND APPROVAL: `record_decision` is one conditional UPDATE
  that matches only while the task is `awaiting_approval` AND the
  presented interrupt id AND diff hash equal the stored pending ones AND
  the approval window is still open. It moves the task out of
  `awaiting_approval` in the same statement, so of N concurrent or
  repeated decisions exactly one matches; the rest update zero rows;
- every state transition is conditional on the state it leaves (a late
  write from an interrupted worker can't resurrect a finished task).
"""

import uuid
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.types.json import Jsonb

from agent_tasks.models import (
    ACTION_RESUME,
    ACTION_START,
    STATUS_AWAITING_APPROVAL,
    STATUS_QUEUED,
    STATUS_RUNNING,
    AgentTask,
)

_CONNECT_TIMEOUT_SECONDS = 5
_COLUMNS = (
    "id, repository_id, target_path, instruction, route, status, action, decision_approved, decided_at, "
    "approvals_granted, proposal_interrupt_id, proposal_diff, proposal_diff_sha256, proposal_relative_path, "
    "proposal_message, proposal_fix_iteration, approval_expires_at, execution_log, final_response, sources, "
    "outcome, error_code, created_at, started_at, heartbeat_at, finished_at, updated_at"
)


def _row_to_task(row: tuple) -> AgentTask:
    return AgentTask(
        id=str(row[0]),
        repository_id=row[1],
        target_path=row[2],
        instruction=row[3],
        route=row[4],
        status=row[5],
        action=row[6],
        decision_approved=row[7],
        decided_at=row[8],
        approvals_granted=row[9],
        proposal_interrupt_id=row[10],
        proposal_diff=row[11],
        proposal_diff_sha256=row[12],
        proposal_relative_path=row[13],
        proposal_message=row[14],
        proposal_fix_iteration=row[15],
        approval_expires_at=row[16],
        execution_log=list(row[17] or []),
        final_response=row[18],
        sources=list(row[19] or []),
        outcome=dict(row[20] or {}),
        error_code=row[21],
        created_at=row[22],
        started_at=row[23],
        heartbeat_at=row[24],
        finished_at=row[25],
        updated_at=row[26],
    )


class AgentTaskStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._database_url, connect_timeout=_CONNECT_TIMEOUT_SECONDS)

    def _one(self, sql: str, params: Dict[str, Any]) -> Optional[AgentTask]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
            conn.commit()
            return _row_to_task(row) if row else None
        finally:
            conn.close()

    # -- submission and reads ---------------------------------------------

    def create(self, *, repository_id: int, target_path: str, instruction: str, route: str) -> Optional[AgentTask]:
        """The new queued task, or None if this repository already has an
        active (queued/running/awaiting-approval) task."""
        return self._one(
            f"""
            INSERT INTO agent_tasks (id, repository_id, target_path, instruction, route, status, action)
            VALUES (%(id)s, %(repository_id)s, %(target_path)s, %(instruction)s, %(route)s, %(status)s, %(action)s)
            ON CONFLICT (repository_id) WHERE status IN ('queued', 'running', 'awaiting_approval') DO NOTHING
            RETURNING {_COLUMNS}
            """,
            {
                "id": str(uuid.uuid4()),
                "repository_id": repository_id,
                "target_path": target_path,
                "instruction": instruction,
                "route": route,
                "status": STATUS_QUEUED,
                "action": ACTION_START,
            },
        )

    def get(self, task_id: str) -> Optional[AgentTask]:
        return self._one(f"SELECT {_COLUMNS} FROM agent_tasks WHERE id = %(id)s", {"id": task_id})

    def list_recent(self, limit: int, repository_id: Optional[int] = None) -> List[AgentTask]:
        where, params = "", {"limit": limit}
        if repository_id is not None:
            where = "WHERE repository_id = %(repository_id)s"
            params["repository_id"] = repository_id
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {_COLUMNS} FROM agent_tasks {where} ORDER BY created_at DESC LIMIT %(limit)s", params)
                return [_row_to_task(r) for r in cur.fetchall()]
        finally:
            conn.close()

    # -- the approval gate ------------------------------------------------

    def record_decision(self, task_id: str, *, approved: bool, interrupt_id: str, diff_sha256: str) -> Optional[AgentTask]:
        """Consumes the pending approval and queues the resume, or returns
        None if ANY condition fails (the caller then explains why from a
        fresh read; that explanation is advisory — this UPDATE is the gate)."""
        if type(approved) is not bool:
            raise TypeError("approved must be a bool")
        return self._one(
            f"""
            UPDATE agent_tasks SET
                status = %(queued)s, action = %(resume)s, decision_approved = %(approved)s, decided_at = now(),
                approvals_granted = approvals_granted + CASE WHEN %(approved)s THEN 1 ELSE 0 END,
                updated_at = now()
            WHERE id = %(id)s
              AND status = %(awaiting)s
              AND proposal_interrupt_id = %(interrupt_id)s
              AND proposal_diff_sha256 = %(diff_sha256)s
              AND approval_expires_at > now()
            RETURNING {_COLUMNS}
            """,
            {
                "id": task_id,
                "approved": approved,
                "interrupt_id": interrupt_id,
                "diff_sha256": diff_sha256,
                "queued": STATUS_QUEUED,
                "resume": ACTION_RESUME,
                "awaiting": STATUS_AWAITING_APPROVAL,
            },
        )

    # -- worker transitions -----------------------------------------------

    def claim_next(self, worker_id: str) -> Optional[AgentTask]:
        return self._one(
            f"""
            UPDATE agent_tasks SET status = %(running)s, worker_id = %(worker_id)s,
                started_at = COALESCE(started_at, now()), heartbeat_at = now(), updated_at = now()
            WHERE id = (
                SELECT id FROM agent_tasks WHERE status = %(queued)s ORDER BY created_at
                FOR UPDATE SKIP LOCKED LIMIT 1
            )
            RETURNING {_COLUMNS}
            """,
            {"running": STATUS_RUNNING, "queued": STATUS_QUEUED, "worker_id": worker_id},
        )

    def heartbeat(self, worker_id: str) -> None:
        self._one(
            f"UPDATE agent_tasks SET heartbeat_at = now() WHERE worker_id = %(worker_id)s AND status = 'running' "
            f"RETURNING {_COLUMNS}",
            {"worker_id": worker_id},
        )

    def await_approval(
        self,
        task_id: str,
        *,
        interrupt_id: str,
        diff: str,
        diff_sha256: str,
        relative_path: Optional[str],
        message: str,
        fix_iteration: int,
        approval_ttl_seconds: int,
        execution_log: List[str],
        outcome: Dict[str, Any],
    ) -> Optional[AgentTask]:
        return self._one(
            f"""
            UPDATE agent_tasks SET status = %(awaiting)s, proposal_interrupt_id = %(interrupt_id)s,
                proposal_diff = %(diff)s, proposal_diff_sha256 = %(diff_sha256)s,
                proposal_relative_path = %(relative_path)s, proposal_message = %(message)s,
                proposal_fix_iteration = %(fix_iteration)s, decision_approved = NULL,
                approval_expires_at = now() + make_interval(secs => %(ttl)s),
                execution_log = %(execution_log)s, outcome = %(outcome)s, updated_at = now()
            WHERE id = %(id)s AND status = %(running)s
            RETURNING {_COLUMNS}
            """,
            {
                "id": task_id,
                "awaiting": STATUS_AWAITING_APPROVAL,
                "running": STATUS_RUNNING,
                "interrupt_id": interrupt_id,
                "diff": diff,
                "diff_sha256": diff_sha256,
                "relative_path": relative_path,
                "message": message,
                "fix_iteration": fix_iteration,
                "ttl": approval_ttl_seconds,
                "execution_log": Jsonb(execution_log),
                "outcome": Jsonb(outcome),
            },
        )

    def finish(
        self,
        task_id: str,
        *,
        status: str,
        final_response: Optional[str],
        sources: List[Dict[str, Any]],
        execution_log: List[str],
        outcome: Dict[str, Any],
    ) -> Optional[AgentTask]:
        return self._one(
            f"""
            UPDATE agent_tasks SET status = %(status)s, final_response = %(final_response)s, sources = %(sources)s,
                execution_log = %(execution_log)s, outcome = %(outcome)s, approval_expires_at = NULL,
                finished_at = now(), updated_at = now()
            WHERE id = %(id)s AND status = %(running)s
            RETURNING {_COLUMNS}
            """,
            {
                "id": task_id,
                "status": status,
                "running": STATUS_RUNNING,
                "final_response": final_response,
                "sources": Jsonb(sources),
                "execution_log": Jsonb(execution_log),
                "outcome": Jsonb(outcome),
            },
        )

    def fail(self, task_id: str, error_code: str) -> Optional[AgentTask]:
        return self._one(
            f"""
            UPDATE agent_tasks SET status = 'failed', error_code = %(error_code)s, approval_expires_at = NULL,
                finished_at = now(), updated_at = now()
            WHERE id = %(id)s AND status IN ('queued', 'running')
            RETURNING {_COLUMNS}
            """,
            {"id": task_id, "error_code": error_code},
        )

    def recover_stale(self, stale_after_seconds: int) -> List[str]:
        """Expires approvals whose window closed and marks running tasks
        whose worker stopped heartbeating as interrupted. Returns the ids
        moved to a terminal state (their checkpoints can then be deleted)."""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE agent_tasks SET status = 'expired', error_code = 'approval_expired',
                        finished_at = now(), updated_at = now()
                    WHERE status = 'awaiting_approval' AND approval_expires_at <= now()
                    RETURNING id
                    """
                )
                ids = [str(r[0]) for r in cur.fetchall()]
                cur.execute(
                    """
                    UPDATE agent_tasks SET status = 'interrupted', error_code = 'worker_interrupted',
                        approval_expires_at = NULL, finished_at = now(), updated_at = now()
                    WHERE status = 'running' AND heartbeat_at < now() - make_interval(secs => %(secs)s)
                    RETURNING id
                    """,
                    {"secs": stale_after_seconds},
                )
                ids += [str(r[0]) for r in cur.fetchall()]
            conn.commit()
            return ids
        finally:
            conn.close()

    # BackgroundWorker's default recovery hook name (jobs/worker.py).
    def fail_stale_running(self, stale_after_seconds: int) -> int:
        return len(self.recover_stale(stale_after_seconds))
