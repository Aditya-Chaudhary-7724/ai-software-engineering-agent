"""The central tracer: creates traces/spans, threads correlation via a
`contextvars` context (so code with no direct access to the current
agent state — a wrapped `LLMProvider`, a `ToolRegistry` — can still
attach its span to the right place in the trace without changing its
own call signature), and hands finished spans/traces to a `Recorder`
(and, optionally, external `TraceExporter`s).

FAIL-SAFE BY DESIGN — this is the single most important property of
this module: every public method catches and swallows any exception
raised by its OWN bookkeeping (building the span/trace object,
sanitizing attributes, writing to the recorder/exporter) and logs it at
WARNING via the standard `logging` module instead of propagating. A
bug in tracing must never break the agent it's observing.

This fail-safety deliberately does NOT swallow the WRAPPED business
logic's own exceptions — `span()` re-raises whatever the code inside
it raises, after recording it. The one exception type treated
specially is LangGraph's `GraphBubbleUp` (the base of `GraphInterrupt`,
used internally by `langgraph.types.interrupt()` to pause a graph) —
this is normal LangGraph control flow, not an error, so a span that
raises it is marked "interrupted", not "error", before being re-raised
untouched.
"""

import contextvars
import logging
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from langgraph.errors import GraphBubbleUp

from observability.exporters.base import TraceExporter
from observability.models import (
    STATUS_ERROR,
    STATUS_INTERRUPTED,
    STATUS_OK,
    STATUS_RUNNING,
    Span,
    SpanEvent,
    Trace,
)
from observability.recorder import Recorder
from observability.redaction import redact_text, sanitize_metadata

logger = logging.getLogger("observability.tracer")

_current_trace_id: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("current_trace_id", default=None)
_current_span_id: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("current_span_id", default=None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_ms(start_iso: str, end_iso: str) -> Optional[float]:
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
        return (end - start).total_seconds() * 1000.0
    except (ValueError, TypeError):
        return None


class Tracer:
    def __init__(self, recorder: Recorder, exporters: Optional[List[TraceExporter]] = None) -> None:
        self._recorder = recorder
        self._exporters = exporters or []
        # In-flight traces, keyed by trace_id, so spans recorded across
        # multiple AgentService.run()/resume() calls for the same
        # thread_id all accumulate onto the same Trace object.
        self._traces: Dict[str, Trace] = {}
        # Currently-open spans, keyed by span_id — a span only moves
        # into `Trace.spans` once it finishes (see `span()`'s finally
        # block), so `record_event` needs this separate registry to
        # attach an event to a span that's still in progress.
        self._open_spans: Dict[str, Span] = {}

    def start_trace(self, trace_id: str, name: str, attributes: Optional[Dict[str, Any]] = None) -> str:
        """Idempotent: calling this again for a `trace_id` that's already
        open (e.g. a second `resume()` on the same thread) is a no-op —
        the existing in-flight Trace just keeps accumulating spans.
        """
        try:
            if trace_id in self._traces:
                return trace_id
            self._traces[trace_id] = Trace(
                trace_id=trace_id,
                name=name,
                started_at=_now_iso(),
                attributes=sanitize_metadata(attributes or {}),
            )
        except Exception:
            logger.warning("observability: failed to start trace %s", trace_id, exc_info=True)
        return trace_id

    def finish_trace(self, trace_id: str, status: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """Flushes the trace's current state to the recorder (and any
        exporters). Safe to call more than once for the same trace_id
        with a non-terminal status (e.g. "awaiting_approval" after each
        `resume()`) — only a terminal status ("completed"/"failed") sets
        `ended_at`/`duration_ms` and releases the in-memory entry.
        """
        try:
            trace = self._traces.get(trace_id)
            if trace is None:
                return
            trace.status = status
            if attributes:
                trace.attributes.update(sanitize_metadata(attributes))

            is_terminal = status in ("completed", "failed")
            if is_terminal:
                trace.ended_at = _now_iso()
                trace.duration_ms = _duration_ms(trace.started_at, trace.ended_at)

            self._recorder.record_trace(trace)
            for exporter in self._exporters:
                try:
                    exporter.export(trace)
                except Exception:
                    logger.warning("observability: exporter %s failed", type(exporter).__name__, exc_info=True)

            if is_terminal:
                self._traces.pop(trace_id, None)
        except Exception:
            logger.warning("observability: failed to finish trace %s", trace_id, exc_info=True)

    def current_trace_id(self) -> Optional[str]:
        return _current_trace_id.get()

    @contextmanager
    def span(
        self, trace_id: Optional[str], name: str, kind: str, attributes: Optional[Dict[str, Any]] = None
    ) -> Iterator[Span]:
        """The one method that touches business logic's control flow —
        everything else in this class only ever wraps its OWN
        bookkeeping in try/except. `trace_id=None` attaches to whatever
        trace is currently active in context (set by an enclosing
        `span()` call), falling back to an ad-hoc, unrecorded-trace span
        if none is active — this is what lets `TracedLLMProvider` and
        `ToolRegistry` create correctly-nested spans without needing a
        trace_id threaded into their own call signatures.
        """
        resolved_trace_id = trace_id or self.current_trace_id() or "untraced"
        span_id = str(uuid.uuid4())
        parent_span_id = _current_span_id.get()
        span = Span(
            span_id=span_id,
            trace_id=resolved_trace_id,
            parent_span_id=parent_span_id,
            name=name,
            kind=kind,
            start_time=_now_iso(),
        )
        try:
            span.attributes = sanitize_metadata(attributes or {})
        except Exception:
            logger.warning("observability: failed to sanitize span attributes for %s", name, exc_info=True)

        token_trace = _current_trace_id.set(resolved_trace_id)
        token_span = _current_span_id.set(span_id)
        self._open_spans[span_id] = span
        start = time.monotonic()
        try:
            yield span
            if span.status == STATUS_RUNNING:
                span.status = STATUS_OK
        except GraphBubbleUp:
            span.status = STATUS_INTERRUPTED
            raise
        except Exception as exc:
            span.status = STATUS_ERROR
            span.error = redact_text(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            _current_span_id.reset(token_span)
            _current_trace_id.reset(token_trace)
            self._open_spans.pop(span_id, None)
            try:
                span.end_time = _now_iso()
                span.duration_ms = (time.monotonic() - start) * 1000.0
                span.attributes = sanitize_metadata(span.attributes)
                trace = self._traces.get(resolved_trace_id)
                if trace is not None:
                    trace.spans.append(span)
                self._recorder.record_span(span)
                logger.info(
                    "span finished",
                    extra={
                        "trace_id": span.trace_id,
                        "span_id": span.span_id,
                        "parent_span_id": span.parent_span_id,
                        "span_name": span.name,
                        "span_kind": span.kind,
                        "span_status": span.status,
                        "duration_ms": span.duration_ms,
                        **span.attributes,
                    },
                )
            except Exception:
                logger.warning("observability: failed to finalize span %s", name, exc_info=True)

    def record_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """A point-in-time occurrence attached to the currently active
        (still-open) span, if any — a safe no-op if no span is active.
        """
        try:
            span_id = _current_span_id.get()
            if span_id is None:
                return
            span = self._open_spans.get(span_id)
            if span is None:
                return
            event = SpanEvent(timestamp=_now_iso(), name=name, attributes=sanitize_metadata(attributes or {}))
            span.events.append(event)
            logger.info(
                "event recorded",
                extra={"trace_id": span.trace_id, "span_id": span_id, "event_name": name, **event.attributes},
            )
        except Exception:
            logger.warning("observability: failed to record event %s", name, exc_info=True)
