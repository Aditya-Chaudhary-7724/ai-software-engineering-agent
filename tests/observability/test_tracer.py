"""Unit tests for the core Tracer — trace creation, unique run IDs,
span lifecycle, success/failure, nesting/correlation, LangGraph
interrupt handling, and fail-safety when the recorder itself is
broken. No external service involved.
"""

import uuid

import pytest
from langgraph.errors import GraphInterrupt

from observability.models import STATUS_ERROR, STATUS_INTERRUPTED, STATUS_OK
from observability.recorder import InMemoryRecorder, Recorder
from observability.tracer import Tracer


def test_start_trace_creates_a_trace_with_the_given_id():
    tracer = Tracer(InMemoryRecorder())
    trace_id = tracer.start_trace("my-trace-id", "agent_thread")
    assert trace_id == "my-trace-id"


def test_start_trace_is_idempotent_for_the_same_trace_id():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    tracer.start_trace("t1", "agent_thread", attributes={"question": "first"})
    tracer.start_trace("t1", "agent_thread", attributes={"question": "second"})

    with tracer.span("t1", "a", "node"):
        pass
    tracer.finish_trace("t1", "completed")

    trace = recorder.get_trace("t1")
    assert trace.attributes["question"] == "first"  # not overwritten by the second start_trace call


def test_two_traces_get_distinct_unique_ids():
    tracer = Tracer(InMemoryRecorder())
    id1 = tracer.start_trace(str(uuid.uuid4()), "agent_thread")
    id2 = tracer.start_trace(str(uuid.uuid4()), "agent_thread")
    assert id1 != id2


def test_successful_span_is_marked_ok_with_duration():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "a_step", "node") as span:
        pass

    tracer.finish_trace("t1", "completed")
    trace = recorder.get_trace("t1")
    assert len(trace.spans) == 1
    recorded = trace.spans[0]
    assert recorded.status == STATUS_OK
    assert recorded.name == "a_step"
    assert recorded.kind == "node"
    assert recorded.duration_ms is not None
    assert recorded.start_time and recorded.end_time


def test_failed_span_is_marked_error_and_exception_propagates():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    tracer.start_trace("t1", "agent_thread")

    with pytest.raises(ValueError):
        with tracer.span("t1", "a_step", "node"):
            raise ValueError("boom")

    tracer.finish_trace("t1", "failed")
    trace = recorder.get_trace("t1")
    assert trace.spans[0].status == STATUS_ERROR
    assert "boom" in trace.spans[0].error


def test_a_langgraph_interrupt_marks_the_span_interrupted_not_error():
    """GraphInterrupt is normal LangGraph control flow (used by
    `interrupt()` to pause a graph for human approval) — it must be
    treated differently from a real error.
    """
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    tracer.start_trace("t1", "agent_thread")

    with pytest.raises(GraphInterrupt):
        with tracer.span("t1", "human_approval", "approval"):
            raise GraphInterrupt([])

    tracer.finish_trace("t1", "awaiting_approval")
    trace = recorder.get_trace("t1")
    assert trace.spans[0].status == STATUS_INTERRUPTED
    assert trace.spans[0].error is None


def test_nested_spans_get_correct_parent_child_relationship():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "outer", "node") as outer:
        with tracer.span(None, "inner", "llm") as inner:
            pass

    tracer.finish_trace("t1", "completed")
    trace = recorder.get_trace("t1")
    inner_span = next(s for s in trace.spans if s.name == "inner")
    outer_span = next(s for s in trace.spans if s.name == "outer")
    assert inner_span.parent_span_id == outer_span.span_id
    assert inner_span.trace_id == "t1"  # inherited from context, not passed explicitly


def test_sibling_spans_at_the_same_level_have_no_parent():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "first", "node"):
        pass
    with tracer.span("t1", "second", "node"):
        pass

    tracer.finish_trace("t1", "completed")
    trace = recorder.get_trace("t1")
    assert all(s.parent_span_id is None for s in trace.spans)


def test_context_is_restored_after_a_span_exits():
    """A span's contextvar changes must not leak into code that runs
    after the `with` block exits — otherwise a later, unrelated span
    would be incorrectly nested under a finished one.
    """
    tracer = Tracer(InMemoryRecorder())
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "a", "node"):
        assert tracer.current_trace_id() == "t1"
    assert tracer.current_trace_id() is None


def test_multiple_resumes_accumulate_onto_the_same_trace():
    """Simulates AgentService.run() -> resume() -> resume(): each call
    opens a new top-level span but all of them must land in the SAME
    trace, correlated by trace_id.
    """
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    tracer.start_trace("thread-1", "agent_thread")

    with tracer.span("thread-1", "agent_run", "agent"):
        pass
    tracer.finish_trace("thread-1", "awaiting_approval")

    with tracer.span("thread-1", "agent_resume", "approval"):
        pass
    tracer.finish_trace("thread-1", "completed")

    trace = recorder.get_trace("thread-1")
    assert trace.status == "completed"
    assert len(trace.spans) == 2
    assert trace.ended_at is not None


def test_finish_trace_with_non_terminal_status_keeps_trace_open():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    tracer.start_trace("t1", "agent_thread")

    tracer.finish_trace("t1", "awaiting_approval")

    trace = recorder.get_trace("t1")
    assert trace.status == "awaiting_approval"
    assert trace.ended_at is None
    assert tracer._traces.get("t1") is not None  # still open internally for a future resume


def test_record_event_attaches_to_the_currently_open_span():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "a", "node"):
        tracer.record_event("checkpoint", {"progress": "halfway"})

    tracer.finish_trace("t1", "completed")
    trace = recorder.get_trace("t1")
    assert len(trace.spans[0].events) == 1
    assert trace.spans[0].events[0].name == "checkpoint"
    assert trace.spans[0].events[0].attributes["progress"] == "halfway"


def test_record_event_with_no_active_span_is_a_safe_no_op():
    tracer = Tracer(InMemoryRecorder())
    tracer.record_event("orphan_event")  # must not raise


def test_span_attributes_are_sanitized_before_reaching_the_recorder():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "a", "tool", attributes={"api_key": "super-secret"}):
        pass

    tracer.finish_trace("t1", "completed")
    trace = recorder.get_trace("t1")
    assert trace.spans[0].attributes["api_key"] == "***REDACTED***"


def test_attributes_set_inside_the_span_block_are_also_sanitized():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "a", "tool") as span:
        span.attributes["password"] = "hunter2"

    tracer.finish_trace("t1", "completed")
    trace = recorder.get_trace("t1")
    assert trace.spans[0].attributes["password"] == "***REDACTED***"


class _BrokenRecorder(Recorder):
    """A recorder that always fails — proves the Tracer's fail-safety,
    not merely asserts it in a docstring.
    """

    def record_span(self, span):
        raise RuntimeError("disk is on fire")

    def record_trace(self, trace):
        raise RuntimeError("disk is on fire")


def test_a_broken_recorder_never_propagates_out_of_span_or_finish_trace():
    tracer = Tracer(_BrokenRecorder())
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "a", "node"):
        pass  # must not raise despite record_span failing internally

    tracer.finish_trace("t1", "completed")  # must not raise despite record_trace failing internally


def test_a_broken_recorder_does_not_prevent_the_wrapped_business_logic_from_running():
    tracer = Tracer(_BrokenRecorder())
    tracer.start_trace("t1", "agent_thread")

    executed = []
    with tracer.span("t1", "a", "node"):
        executed.append("business logic ran")

    assert executed == ["business logic ran"]


def test_a_broken_recorder_still_lets_a_real_exception_from_business_logic_propagate():
    tracer = Tracer(_BrokenRecorder())
    tracer.start_trace("t1", "agent_thread")

    with pytest.raises(ValueError, match="real error"):
        with tracer.span("t1", "a", "node"):
            raise ValueError("real error")


def test_a_failing_exporter_does_not_break_finish_trace():
    from observability.exporters.base import TraceExporter

    class _BrokenExporter(TraceExporter):
        def export(self, trace):
            raise RuntimeError("network down")

    tracer = Tracer(InMemoryRecorder(), exporters=[_BrokenExporter()])
    tracer.start_trace("t1", "agent_thread")
    with tracer.span("t1", "a", "node"):
        pass

    tracer.finish_trace("t1", "completed")  # must not raise
