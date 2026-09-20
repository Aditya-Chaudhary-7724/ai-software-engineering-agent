"""Renders a `Trace` as a human-readable, indented span tree — what
`backend/scripts/inspect_trace.py` prints.
"""

from typing import Dict, List, Optional

from observability.models import Trace

_STATUS_MARKERS = {"ok": "OK", "error": "FAIL", "interrupted": "PAUSED", "running": "RUNNING"}


def render_trace(trace: Trace) -> str:
    lines: List[str] = []
    lines.append(f"Trace {trace.trace_id} — {trace.name} [{trace.status.upper()}]")
    lines.append(f"  started_at:  {trace.started_at}")
    lines.append(f"  ended_at:    {trace.ended_at or '(not yet finished)'}")
    if trace.duration_ms is not None:
        lines.append(f"  duration:    {trace.duration_ms:.1f} ms")
    if trace.attributes:
        lines.append(f"  attributes:  {trace.attributes}")
    lines.append(f"  spans:       {len(trace.spans)}")
    lines.append("")

    by_parent: Dict[Optional[str], List] = {}
    for span in trace.spans:
        by_parent.setdefault(span.parent_span_id, []).append(span)
    for children in by_parent.values():
        children.sort(key=lambda s: s.start_time)

    def render_span(span, depth: int) -> None:
        indent = "  " * depth
        marker = _STATUS_MARKERS.get(span.status, span.status.upper())
        duration = f"{span.duration_ms:.1f}ms" if span.duration_ms is not None else "?"
        lines.append(f"{indent}- [{marker}] {span.name} ({span.kind})  {duration}  id={span.span_id[:8]}")
        if span.attributes:
            lines.append(f"{indent}    attributes: {span.attributes}")
        if span.events:
            for event in span.events:
                lines.append(f"{indent}    event: {event.name} {event.attributes}")
        if span.error:
            lines.append(f"{indent}    error: {span.error}")
        for child in by_parent.get(span.span_id, []):
            render_span(child, depth + 1)

    if not trace.spans:
        lines.append("  (no spans recorded)")
    for root_span in by_parent.get(None, []):
        render_span(root_span, 1)

    return "\n".join(lines)
