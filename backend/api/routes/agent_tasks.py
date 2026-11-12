"""Agent tasks and human approval (frontend phase F6).

An explicit Task workflow, SEPARATE from the read-only Ask (routes/ask.py
imports nothing from here, and vice versa). Submitting a task only ENQUEUES
it for the in-process agent worker (agent_tasks/executor.py); the client
polls `GET /agent-tasks/{id}`.

Trust boundaries (same as F3/F4):
- the repository root comes from the trusted id lookup, never the client;
  request bodies forbid unknown fields;
- responses use `api.schemas.AgentTask` as an output allowlist (no path
  field), and every free-text field produced by the agent is scrubbed of
  server paths and secret-shaped values at read time. The proposed DIFF is
  deliberately NOT rewritten: it is exactly what would be written, and the
  approval is bound to its hash.

Approval (`POST /agent-tasks/{id}/decision`):
- `approved` must be a JSON boolean (`StrictBool`: "no", 1, {}, null are
  422), and the body must echo the pending `interrupt_id` and the SHA-256 of
  the diff the reviewer saw;
- acceptance is ONE conditional UPDATE (agent_tasks/store.py) — stale,
  changed, reused, expired and concurrent duplicate decisions match zero
  rows and get 409; this endpoint never runs the agent itself;
- the worker then resumes via `AgentService.resume`, which re-verifies the
  interrupt id and diff hash against the checkpoint, and
  `human_approval_node` checks the hash once more.
No GitHub push/PR endpoint exists, here or anywhere under /api/v1.
"""

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import psycopg
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from agent.approval import hashes_match
from agent.classification import classify_task
from agent_tasks import models as task_models
from agent_tasks.store import AgentTaskStore

from api.dependencies import get_database_url, require_repository_on_disk
from api.errors import APIError
from api.repository_catalog import RepositoryRecord
from api.schemas import (
    AgentRoutePreview,
    AgentTask,
    AgentTaskList,
    AgentTaskOutcome,
    AgentTaskProposal,
    AgentTaskSource,
)
from api.trace_sanitizer import scrub_paths
from observability.redaction import redact_secrets

logger = logging.getLogger("api.agent_tasks")

router = APIRouter(tags=["agent-tasks"])

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_INTERRUPT_ID_PATTERN = r"^[0-9a-f]{1,128}$"


class RoutePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(..., min_length=1, max_length=task_models.MAX_INSTRUCTION_CHARS)


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(..., min_length=1, max_length=task_models.MAX_INSTRUCTION_CHARS)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: StrictBool
    interrupt_id: str = Field(..., pattern=_INTERRUPT_ID_PATTERN)
    diff_sha256: str = Field(..., pattern=_SHA256_PATTERN)


def get_agent_worker(request: Request):
    """Without a running agent worker a queued task (or a recorded decision)
    would never be processed, so both are refused instead (503)."""
    worker = getattr(request.app.state, "agent_worker", None)
    if worker is None or not worker.is_running:
        raise APIError(503, "agent_worker_unavailable")
    return worker


def _with_db_errors(operation):
    try:
        return operation()
    except psycopg.errors.UndefinedTable:
        raise APIError(503, "schema_not_initialized")
    except psycopg.OperationalError:
        raise APIError(503, "database_unavailable")


def _canonical_task_id(task_id: str) -> str:
    try:
        canonical = str(uuid.UUID(task_id))
    except (ValueError, AttributeError):
        raise APIError(400, "invalid_task_id")
    if canonical != task_id:
        raise APIError(400, "invalid_task_id")
    return canonical


def _clean_instruction(value: str) -> str:
    instruction = value.strip()
    if not instruction:
        raise APIError(422, "invalid_instruction")
    return instruction


# -- serialization ---------------------------------------------------------


def _known_roots(task: task_models.AgentTask) -> List[str]:
    roots = [task.target_path]
    try:
        resolved = str(Path(task.target_path).resolve())
        if resolved != task.target_path:
            roots.append(resolved)
    except OSError:
        pass
    # Longest first, so a resolved root containing the other is replaced whole.
    return sorted({r for r in roots if r.startswith("/") and len(r) > 1}, key=len, reverse=True)


def _scrub(value: Optional[str], roots: List[str]) -> Optional[str]:
    return None if value is None else redact_secrets(scrub_paths(value, roots))


def _proposal_state(task: task_models.AgentTask, now: datetime) -> str:
    expires = task.approval_expires_at
    if task.status == task_models.STATUS_AWAITING_APPROVAL:
        return "pending" if expires is not None and expires > now else "expired"
    if task.status == task_models.STATUS_EXPIRED:
        return "expired"
    if task.decision_approved is True:
        return "approved"
    if task.decision_approved is False:
        return "rejected"
    return "closed"


def _source(raw: dict, roots: List[str]) -> Optional[AgentTaskSource]:
    path = raw.get("relative_path")
    if not isinstance(path, str) or path.startswith("/"):
        return None
    found_via = raw.get("found_via")
    if not isinstance(found_via, list):
        found_via = []
    return AgentTaskSource(
        relative_path=_scrub(path, roots) or "",
        start_line=raw.get("start_line") if isinstance(raw.get("start_line"), int) else None,
        end_line=raw.get("end_line") if isinstance(raw.get("end_line"), int) else None,
        symbol_name=raw.get("symbol_name") if isinstance(raw.get("symbol_name"), str) else None,
        found_via=[str(v) for v in found_via],
    )


def to_api(task: task_models.AgentTask, now: Optional[datetime] = None) -> AgentTask:
    now = now or datetime.now(timezone.utc)
    roots = _known_roots(task)
    outcome = task.outcome
    proposal = None
    if task.proposal_diff is not None and task.proposal_diff_sha256:
        state = _proposal_state(task, now)
        proposal = AgentTaskProposal(
            state=state,
            # Only a pending proposal can be decided, so only it carries the id.
            interrupt_id=task.proposal_interrupt_id if state == "pending" else None,
            relative_path=task.proposal_relative_path,
            diff=task.proposal_diff,
            diff_sha256=task.proposal_diff_sha256,
            message=_scrub(task.proposal_message, roots),
            fix_iteration=task.proposal_fix_iteration or 0,
            expires_at=task.approval_expires_at if state == "pending" else None,
        )
    sources = [s for s in (_source(raw, roots) for raw in task.sources if isinstance(raw, dict)) if s is not None]
    return AgentTask(
        id=task.id,
        trace_id=task.id,
        repository_id=task.repository_id,
        instruction=task.instruction,
        route=task.route,
        status=task.status,
        error_code=task.error_code,
        execution_log=[_scrub(str(entry), roots) or "" for entry in task.execution_log],
        final_response=_scrub(task.final_response, roots),
        sources=sources,
        outcome=AgentTaskOutcome(
            generator=outcome.get("generator"),
            is_placeholder=outcome.get("generator") == "stub",
            approved=outcome.get("approved"),
            applied=outcome.get("applied"),
            changes_applied=int(outcome.get("changes_applied") or 0),
            test_ran=bool(outcome.get("test_ran")),
            test_passed=outcome.get("test_passed"),
            test_skipped_reason=outcome.get("test_skipped_reason"),
        ),
        proposal=proposal,
        approvals_granted=task.approvals_granted,
        decided_at=task.decided_at,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        updated_at=task.updated_at,
    )


def _json(task: task_models.AgentTask, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=to_api(task).model_dump(mode="json"))


# -- routes ------------------------------------------------------------------


@router.post("/agent-tasks/route-preview", response_model=AgentRoutePreview)
def preview_route(request: RoutePreviewRequest) -> AgentRoutePreview:
    """Pure and read-only: the same deterministic classifier the graph's
    task_analyzer node uses, so the preview is the route the task takes."""
    route = classify_task(_clean_instruction(request.instruction))
    return AgentRoutePreview(
        route=route,
        supported=route in task_models.SUPPORTED_ROUTES,
        requires_approval=route == task_models.ROUTE_MODIFY,
    )


@router.post("/repositories/{repository_id}/agent-tasks", response_model=AgentTask, status_code=202)
def submit_task(
    request: TaskRequest,
    record: RepositoryRecord = Depends(require_repository_on_disk),
    database_url: str = Depends(get_database_url),
    worker=Depends(get_agent_worker),
):
    instruction = _clean_instruction(request.instruction)
    route = classify_task(instruction)
    if route not in task_models.SUPPORTED_ROUTES:
        raise APIError(422, "route_not_supported")
    task = _with_db_errors(
        lambda: AgentTaskStore(database_url).create(
            repository_id=record.id, target_path=record.root_path, instruction=instruction, route=route
        )
    )
    if task is None:
        raise APIError(409, "agent_task_active")
    worker.notify()
    return _json(task, 202)


@router.get("/agent-tasks", response_model=AgentTaskList)
def list_tasks(
    repository_id: Optional[int] = Query(None, ge=1),
    limit: int = Query(20, ge=1, le=100),
    database_url: str = Depends(get_database_url),
) -> AgentTaskList:
    tasks = _with_db_errors(lambda: AgentTaskStore(database_url).list_recent(limit, repository_id))
    return AgentTaskList(tasks=[to_api(t) for t in tasks])


@router.get("/agent-tasks/{task_id}", response_model=AgentTask)
def get_task(task_id: str, database_url: str = Depends(get_database_url)) -> AgentTask:
    canonical = _canonical_task_id(task_id)
    task = _with_db_errors(lambda: AgentTaskStore(database_url).get(canonical))
    if task is None:
        raise APIError(404, "agent_task_not_found")
    return to_api(task)


@router.post("/agent-tasks/{task_id}/decision", response_model=AgentTask, status_code=202)
def decide(
    task_id: str,
    request: DecisionRequest,
    database_url: str = Depends(get_database_url),
    worker=Depends(get_agent_worker),
):
    canonical = _canonical_task_id(task_id)
    store = AgentTaskStore(database_url)
    accepted = _with_db_errors(
        lambda: store.record_decision(
            canonical, approved=request.approved, interrupt_id=request.interrupt_id, diff_sha256=request.diff_sha256
        )
    )
    if accepted is None:
        current = _with_db_errors(lambda: store.get(canonical))
        code = refusal_code(current, request.interrupt_id, request.diff_sha256)
        logger.info("approval decision refused", extra={"task_id": canonical, "error_code": code})
        raise APIError(404 if current is None else 409, code)
    logger.info("approval decision recorded", extra={"task_id": canonical, "approved": request.approved})
    worker.notify()
    return _json(accepted, 202)


def refusal_code(task: Optional[task_models.AgentTask], interrupt_id: str, diff_sha256: str) -> str:
    """WHY a decision matched no row — for the operator only. The atomic
    UPDATE already refused it; nothing here can turn a refusal into an
    acceptance."""
    if task is None:
        return "agent_task_not_found"
    if task.status != task_models.STATUS_AWAITING_APPROVAL:
        return "approval_not_pending"
    if task.proposal_interrupt_id != interrupt_id:
        return "approval_stale"
    if not task.proposal_diff_sha256 or not hashes_match(diff_sha256, task.proposal_diff_sha256):
        return "approval_diff_mismatch"
    return "approval_expired"
