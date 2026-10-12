"""Agent execution traces recorded by Phase 13's `JSONFileRecorder`.

Read-only. Trace IDs are validated as canonical UUIDs BEFORE any
filesystem access, so a trace id can never select a path (`../x`,
absolute paths, or any non-UUID filename). Every returned string passes
through `api.trace_sanitizer` — server paths removed, secrets
re-redacted. Traces contain safe execution summaries and span metadata
only; Phase 13 never records prompts, model output, or chain-of-thought.
"""

import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from observability.recorder import JSONFileRecorder

from api.dependencies import get_trace_directory
from api.errors import APIError
from api.schemas import Span, SpanEvent, TraceDetail, TraceList, TraceSummary
from api.trace_sanitizer import known_roots_of, scrub_attributes, scrub_text

logger = logging.getLogger("api.traces")

router = APIRouter(tags=["traces"])

_QUESTION_PREVIEW_CHARS = 200


def _canonical_trace_id(value: str) -> Optional[str]:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
    # Reject non-canonical spellings ("{...}", "urn:uuid:", uppercase,
    # no hyphens) so exactly one string maps to each trace file.
    return value if str(parsed) == value else None


def _trace_files_newest_first(directory: Path) -> List[Path]:
    if not directory.is_dir():
        return []
    candidates = [p for p in directory.glob("*.json") if p.is_file() and _canonical_trace_id(p.stem)]
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)


def _repository_id(attributes: Dict[str, Any]) -> Optional[int]:
    value = attributes.get("repository_id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _summary_fields(trace) -> Dict[str, Any]:
    roots = known_roots_of(trace.attributes)
    question = trace.attributes.get("question")
    return {
        "trace_id": trace.trace_id,
        "name": trace.name,
        "status": trace.status,
        "started_at": trace.started_at,
        "ended_at": trace.ended_at,
        "duration_ms": trace.duration_ms,
        "repository_id": _repository_id(trace.attributes),
        "question": scrub_text(question, roots)[:_QUESTION_PREVIEW_CHARS] if isinstance(question, str) else None,
        "span_count": len(trace.spans),
        "error_span_count": sum(1 for s in trace.spans if s.status == "error"),
    }


def _load(directory: Path, trace_id: str):
    path = directory / f"{trace_id}.json"
    if not path.is_file():
        return None
    try:
        return JSONFileRecorder(directory).load_trace(trace_id)
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("trace file could not be parsed", extra={"trace_id": trace_id})
        raise APIError(500, "trace_unreadable")


@router.get("/traces", response_model=TraceList)
def list_traces(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    directory: Path = Depends(get_trace_directory),
) -> TraceList:
    files = _trace_files_newest_first(directory)
    summaries: List[TraceSummary] = []
    for path in files[offset : offset + limit]:
        try:
            trace = _load(directory, path.stem)
        except APIError:
            continue  # one corrupt file must not hide every other trace
        if trace is not None:
            summaries.append(TraceSummary(**_summary_fields(trace)))
    return TraceList(traces=summaries, total=len(files), limit=limit, offset=offset)


@router.get("/traces/{trace_id}", response_model=TraceDetail)
def get_trace(trace_id: str, directory: Path = Depends(get_trace_directory)) -> TraceDetail:
    canonical = _canonical_trace_id(trace_id)
    if canonical is None:
        raise APIError(400, "invalid_trace_id")
    trace = _load(directory, canonical)
    if trace is None:
        raise APIError(404, "trace_not_found")

    roots = known_roots_of(trace.attributes)
    spans = [
        Span(
            span_id=s.span_id,
            parent_span_id=s.parent_span_id,
            name=s.name,
            kind=s.kind,
            status=s.status,
            start_time=s.start_time,
            end_time=s.end_time,
            duration_ms=s.duration_ms,
            attributes=scrub_attributes(s.attributes, roots),
            error=scrub_text(s.error, roots) if s.error else None,
            events=[
                SpanEvent(timestamp=e.timestamp, name=e.name, attributes=scrub_attributes(e.attributes, roots))
                for e in s.events
            ],
        )
        for s in trace.spans
    ]
    return TraceDetail(
        **_summary_fields(trace),
        attributes=scrub_attributes(trace.attributes, roots),
        spans=spans,
    )
