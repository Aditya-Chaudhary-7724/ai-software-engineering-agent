"""Unit tests for render_trace — hand-built Trace/Span objects, no
service involved.
"""

from observability.models import Span, Trace
from observability.report import render_trace


def test_render_trace_includes_summary_fields():
    trace = Trace(trace_id="t1", name="agent_thread", started_at="2026-01-01T00:00:00+00:00", status="completed")
    text = render_trace(trace)
    assert "t1" in text
    assert "agent_thread" in text
    assert "COMPLETED" in text


def test_render_trace_shows_no_spans_message_when_empty():
    trace = Trace(trace_id="t1", name="agent_thread", started_at="2026-01-01T00:00:00+00:00")
    text = render_trace(trace)
    assert "no spans recorded" in text


def test_render_trace_nests_child_spans_under_their_parent():
    parent = Span(span_id="p", trace_id="t1", parent_span_id=None, name="parent", kind="node", status="ok", start_time="t")
    child = Span(span_id="c", trace_id="t1", parent_span_id="p", name="child", kind="llm", status="ok", start_time="t")
    trace = Trace(trace_id="t1", name="agent_thread", started_at="t", spans=[child, parent])

    text = render_trace(trace)
    parent_index = text.index("parent")
    child_index = text.index("child")
    assert parent_index < child_index
    # The child's line should be indented further than the parent's.
    parent_line = next(line for line in text.splitlines() if "parent" in line and "(node)" in line)
    child_line = next(line for line in text.splitlines() if "child" in line and "(llm)" in line)
    parent_indent = len(parent_line) - len(parent_line.lstrip())
    child_indent = len(child_line) - len(child_line.lstrip())
    assert child_indent > parent_indent


def test_render_trace_shows_error_and_attributes():
    span = Span(
        span_id="s",
        trace_id="t1",
        parent_span_id=None,
        name="failing_step",
        kind="node",
        status="error",
        start_time="t",
        error="ValueError: boom",
        attributes={"task_type": "modify"},
    )
    trace = Trace(trace_id="t1", name="agent_thread", started_at="t", spans=[span])

    text = render_trace(trace)
    assert "FAIL" in text
    assert "ValueError: boom" in text
    assert "task_type" in text
