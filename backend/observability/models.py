"""Plain, dependency-free data models for one trace.

Mutable (not `@dataclass(frozen=True)`), unlike most of this project's
dataclasses: a trace/span is built up incrementally as an agent run
progresses (spans are appended, status/duration are filled in when a
span or trace finishes) — the same "mutated in place" rationale as
`ingestion.models.RepositoryMetadata`/`LanguageStats`.

Deliberately plain str/float/bool/dict/list fields only, no
provider-specific objects — this is what makes a `Trace` trivially
JSON-serializable for `JSONFileRecorder` and safe to hand to an
external exporter without leaking an internal type.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Status values used across Span/Trace. Not a strict Enum, matching
# this project's existing convention for small closed string sets
# (e.g. agent.state.AgentState's task_type/decision fields) — kept as
# a documented constant list for reference rather than enforced typing.
STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_INTERRUPTED = "interrupted"
TRACE_STATUS_COMPLETED = "completed"
TRACE_STATUS_FAILED = "failed"
TRACE_STATUS_AWAITING_APPROVAL = "awaiting_approval"


@dataclass
class SpanEvent:
    """A single point-in-time occurrence within a span (e.g. "retry
    scheduled") — lighter weight than a whole child span when there's
    no meaningful duration to measure.
    """

    timestamp: str
    name: str
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Span:
    """One step in a trace — a node execution, an LLM call, a tool
    call, a sandbox run, etc. `kind` names which conceptual box in this
    project's trace flow (agent -> retrieval -> graph -> llm -> tool ->
    modification -> approval -> sandbox) this span belongs to.
    """

    span_id: str
    trace_id: str
    parent_span_id: Optional[str]
    name: str
    kind: str
    status: str = STATUS_RUNNING
    start_time: str = ""
    end_time: Optional[str] = None
    duration_ms: Optional[float] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    events: List[SpanEvent] = field(default_factory=list)


@dataclass
class Trace:
    """One agent run/thread, end to end — from the first `AgentService.run()`
    call through every `resume()` (human approval, fix-loop retries) to
    a terminal status. `trace_id` is always the same value as the
    `thread_id` a caller already passes to `AgentService` (see
    `agent/service.py`) — reusing an ID this project already requires
    to be unique, rather than inventing a second one.
    """

    trace_id: str
    name: str
    started_at: str
    ended_at: Optional[str] = None
    duration_ms: Optional[float] = None
    status: str = STATUS_RUNNING
    attributes: Dict[str, Any] = field(default_factory=dict)
    spans: List[Span] = field(default_factory=list)
