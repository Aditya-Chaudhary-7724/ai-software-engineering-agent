"""Agent-task data model (frontend phase F6).

`target_path` is the trusted, server-resolved repository root; like
`jobs.models.Job.target_path` it is carried for the worker and never
serialized by the API.

Status lifecycle:

    queued -> running -> completed                  (answer, or modify run to the end)
                      -> awaiting_approval -> [decision] -> queued (action=resume) -> running -> ...
                      -> failed                     (stable error_code)
    awaiting_approval -> expired                    (approval window passed)
    running           -> interrupted                (worker heartbeat went stale)
    running (resume after a rejection) -> rejected

`completed` / `rejected` / `failed` / `expired` / `interrupted` are terminal.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_AWAITING_APPROVAL = "awaiting_approval"
STATUS_COMPLETED = "completed"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
STATUS_INTERRUPTED = "interrupted"
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING, STATUS_AWAITING_APPROVAL)
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_REJECTED, STATUS_FAILED, STATUS_EXPIRED, STATUS_INTERRUPTED)

ACTION_START = "start"
ACTION_RESUME = "resume"

# Routes F6 accepts. The agent's third route, "test" (a standalone sandbox
# test run), is the F7 "Tests & evaluation" scope and is refused here; tests
# still run in the sandbox as the existing post-apply verification step.
ROUTE_ANSWER = "answer"
ROUTE_MODIFY = "modify"
SUPPORTED_ROUTES = (ROUTE_ANSWER, ROUTE_MODIFY)

DEFAULT_APPROVAL_TTL_SECONDS = 3600
MAX_INSTRUCTION_CHARS = 2000


@dataclass(frozen=True)
class AgentTask:
    id: str
    repository_id: int
    target_path: str
    instruction: str
    route: str
    status: str
    action: str
    decision_approved: Optional[bool] = None
    decided_at: Optional[datetime] = None
    approvals_granted: int = 0
    proposal_interrupt_id: Optional[str] = None
    proposal_diff: Optional[str] = None
    proposal_diff_sha256: Optional[str] = None
    proposal_relative_path: Optional[str] = None
    proposal_message: Optional[str] = None
    proposal_fix_iteration: Optional[int] = None
    approval_expires_at: Optional[datetime] = None
    execution_log: List[str] = field(default_factory=list)
    final_response: Optional[str] = None
    sources: List[Dict[str, Any]] = field(default_factory=list)
    outcome: Dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
