"""Recorder abstraction: where finished spans/traces go.

Three implementations, all local and credential-free (no external
observability service is ever required):
- `NullRecorder` — discards everything. The default when a caller
  doesn't need traces persisted; tracing bookkeeping still runs (and is
  still tested), nothing is written anywhere.
- `InMemoryRecorder` — keeps every trace/span in a Python list. Useful
  for tests and short-lived local sessions.
- `JSONFileRecorder` — writes one JSON file per trace under a local
  directory (default `.observability/traces/`, gitignored). This is
  what `backend/scripts/inspect_trace.py` reads from.
"""

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Union

from observability.models import Span, SpanEvent, Trace

DEFAULT_TRACE_DIR = str(Path(__file__).resolve().parents[2] / ".observability" / "traces")


class Recorder(ABC):
    @abstractmethod
    def record_span(self, span: Span) -> None:
        """Called once when an individual span finishes."""

    @abstractmethod
    def record_trace(self, trace: Trace) -> None:
        """Called whenever a trace's current state is flushed — on every
        `Tracer.finish_trace` call, which may happen more than once per
        trace (e.g. once per `AgentService.resume()`), not only when the
        trace reaches a terminal status. Implementations should treat
        this as "persist/overwrite the current snapshot", not "append".
        """


class NullRecorder(Recorder):
    def record_span(self, span: Span) -> None:
        pass

    def record_trace(self, trace: Trace) -> None:
        pass


class InMemoryRecorder(Recorder):
    def __init__(self) -> None:
        self.spans: List[Span] = []
        # Keyed by trace_id so repeated `record_trace` calls for the same
        # trace (across resumes) overwrite rather than accumulate duplicates.
        self._traces_by_id: dict = {}

    def record_span(self, span: Span) -> None:
        self.spans.append(span)

    def record_trace(self, trace: Trace) -> None:
        self._traces_by_id[trace.trace_id] = trace

    @property
    def traces(self) -> List[Trace]:
        return list(self._traces_by_id.values())

    def get_trace(self, trace_id: str) -> Optional[Trace]:
        return self._traces_by_id.get(trace_id)


class JSONFileRecorder(Recorder):
    def __init__(self, directory: Union[str, Path, None] = None) -> None:
        self.directory = Path(directory or DEFAULT_TRACE_DIR)
        self.directory.mkdir(parents=True, exist_ok=True)

    def record_span(self, span: Span) -> None:
        # Spans are embedded in the trace document written by
        # record_trace — no separate per-span file for this simple,
        # local-only recorder.
        pass

    def record_trace(self, trace: Trace) -> None:
        path = self.directory / f"{trace.trace_id}.json"
        path.write_text(json.dumps(_trace_to_dict(trace), indent=2, default=str))

    def load_trace(self, trace_id: str) -> Optional[Trace]:
        path = self.directory / f"{trace_id}.json"
        if not path.exists():
            return None
        return _trace_from_dict(json.loads(path.read_text()))

    def list_trace_ids(self) -> List[str]:
        return sorted(p.stem for p in self.directory.glob("*.json"))


def _trace_to_dict(trace: Trace) -> dict:
    return {
        "trace_id": trace.trace_id,
        "name": trace.name,
        "started_at": trace.started_at,
        "ended_at": trace.ended_at,
        "duration_ms": trace.duration_ms,
        "status": trace.status,
        "attributes": trace.attributes,
        "spans": [_span_to_dict(s) for s in trace.spans],
    }


def _span_to_dict(span: Span) -> dict:
    return {
        "span_id": span.span_id,
        "trace_id": span.trace_id,
        "parent_span_id": span.parent_span_id,
        "name": span.name,
        "kind": span.kind,
        "status": span.status,
        "start_time": span.start_time,
        "end_time": span.end_time,
        "duration_ms": span.duration_ms,
        "attributes": span.attributes,
        "error": span.error,
        "events": [{"timestamp": e.timestamp, "name": e.name, "attributes": e.attributes} for e in span.events],
    }


def _trace_from_dict(data: dict) -> Trace:
    spans = [
        Span(
            span_id=s["span_id"],
            trace_id=s["trace_id"],
            parent_span_id=s.get("parent_span_id"),
            name=s["name"],
            kind=s["kind"],
            status=s.get("status", "running"),
            start_time=s.get("start_time", ""),
            end_time=s.get("end_time"),
            duration_ms=s.get("duration_ms"),
            attributes=s.get("attributes") or {},
            error=s.get("error"),
            events=[SpanEvent(**e) for e in s.get("events", [])],
        )
        for s in data.get("spans", [])
    ]
    return Trace(
        trace_id=data["trace_id"],
        name=data.get("name", ""),
        started_at=data.get("started_at", ""),
        ended_at=data.get("ended_at"),
        duration_ms=data.get("duration_ms"),
        status=data.get("status", "running"),
        attributes=data.get("attributes") or {},
        spans=spans,
    )
