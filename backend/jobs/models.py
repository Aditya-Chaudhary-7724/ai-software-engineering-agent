"""Background-job data model (frontend phase F3).

Plain dataclasses, matching the rest of the backend. `target_path` is the
trusted, server-resolved repository root a job operates on; it is carried
here for the worker but never serialized by the API (see api/schemas.py).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

JOB_TYPE_INGEST_GITHUB = "ingest_github"
JOB_TYPE_REINDEX = "reindex"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED)

# Stages are reported only when the worker actually enters them — no
# estimated percentages are ever invented.
STAGE_QUEUED = "queued"
STAGE_FETCHING_METADATA = "fetching_metadata"
STAGE_CLONING = "cloning"
STAGE_INDEXING = "indexing"
STAGE_BUILDING_GRAPH = "building_graph"
STAGE_DONE = "done"

GRAPH_NOT_REQUESTED = "not_requested"
GRAPH_PENDING = "pending"
GRAPH_COMPLETED = "completed"
GRAPH_FAILED = "failed"
# Requested, but the job failed before the graph step was reached.
GRAPH_NOT_RUN = "not_run"


@dataclass(frozen=True)
class Job:
    id: str
    job_type: str
    status: str
    stage: str
    target_path: str
    github_full_name: Optional[str]
    repository_id: Optional[int]
    build_graph: bool
    graph_status: str
    progress: Dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None


class JobFailure(Exception):
    """Raised by job execution with a STABLE, SAFE error code. The code is
    what gets stored and returned; the underlying exception's message (which
    may contain paths, URLs, or command output) is only logged server-side."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
