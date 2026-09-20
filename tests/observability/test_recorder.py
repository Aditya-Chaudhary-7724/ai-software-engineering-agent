"""Pure unit tests for the three Recorder implementations — no
external service involved, only the local filesystem (tmp_path) for
JSONFileRecorder.
"""

from observability.models import Span, Trace
from observability.recorder import InMemoryRecorder, JSONFileRecorder, NullRecorder


def _sample_trace() -> Trace:
    span = Span(
        span_id="span-1",
        trace_id="trace-1",
        parent_span_id=None,
        name="task_analyzer",
        kind="node",
        status="ok",
        start_time="2026-01-01T00:00:00+00:00",
        end_time="2026-01-01T00:00:01+00:00",
        duration_ms=1000.0,
        attributes={"task_type": "answer"},
    )
    return Trace(
        trace_id="trace-1",
        name="agent_thread",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:01+00:00",
        duration_ms=1000.0,
        status="completed",
        attributes={"question": "hello"},
        spans=[span],
    )


def test_null_recorder_discards_everything():
    recorder = NullRecorder()
    recorder.record_trace(_sample_trace())
    recorder.record_span(Span(span_id="s", trace_id="t", parent_span_id=None, name="n", kind="node"))
    # Nothing to assert beyond "did not raise" — NullRecorder has no state.


def test_in_memory_recorder_stores_and_retrieves_a_trace():
    recorder = InMemoryRecorder()
    trace = _sample_trace()

    recorder.record_trace(trace)

    assert recorder.get_trace("trace-1") is trace
    assert recorder.get_trace("does-not-exist") is None


def test_in_memory_recorder_overwrites_on_repeated_record_trace():
    recorder = InMemoryRecorder()
    trace = _sample_trace()
    recorder.record_trace(trace)

    trace.status = "failed"
    recorder.record_trace(trace)

    assert len(recorder.traces) == 1
    assert recorder.get_trace("trace-1").status == "failed"


def test_in_memory_recorder_accumulates_spans():
    recorder = InMemoryRecorder()
    recorder.record_span(Span(span_id="a", trace_id="t", parent_span_id=None, name="n1", kind="node"))
    recorder.record_span(Span(span_id="b", trace_id="t", parent_span_id=None, name="n2", kind="node"))

    assert len(recorder.spans) == 2


def test_json_file_recorder_writes_and_loads_a_trace(tmp_path):
    recorder = JSONFileRecorder(tmp_path)
    trace = _sample_trace()

    recorder.record_trace(trace)
    loaded = recorder.load_trace("trace-1")

    assert loaded is not None
    assert loaded.trace_id == "trace-1"
    assert loaded.status == "completed"
    assert loaded.attributes == {"question": "hello"}
    assert len(loaded.spans) == 1
    assert loaded.spans[0].name == "task_analyzer"
    assert loaded.spans[0].attributes == {"task_type": "answer"}


def test_json_file_recorder_returns_none_for_missing_trace(tmp_path):
    recorder = JSONFileRecorder(tmp_path)
    assert recorder.load_trace("does-not-exist") is None


def test_json_file_recorder_list_trace_ids(tmp_path):
    recorder = JSONFileRecorder(tmp_path)
    recorder.record_trace(_sample_trace())

    other = _sample_trace()
    other.trace_id = "trace-2"
    recorder.record_trace(other)

    assert recorder.list_trace_ids() == ["trace-1", "trace-2"]


def test_json_file_recorder_overwrites_the_same_trace_file(tmp_path):
    recorder = JSONFileRecorder(tmp_path)
    trace = _sample_trace()
    recorder.record_trace(trace)

    trace.status = "failed"
    recorder.record_trace(trace)

    assert recorder.load_trace("trace-1").status == "failed"
    assert len(recorder.list_trace_ids()) == 1


def test_json_file_recorder_creates_its_directory_if_missing(tmp_path):
    target = tmp_path / "does" / "not" / "exist"
    recorder = JSONFileRecorder(target)
    assert target.is_dir()


def test_json_file_recorder_handles_non_json_native_attribute_values(tmp_path):
    """A span/trace attribute could end up holding something json.dumps
    can't natively serialize (e.g. a value that slipped past
    sanitize_metadata) — the recorder must not crash the agent over it.
    """
    from pathlib import Path

    recorder = JSONFileRecorder(tmp_path)
    trace = _sample_trace()
    trace.attributes["odd_value"] = Path("/tmp/x")  # not natively JSON-serializable

    recorder.record_trace(trace)  # must not raise

    loaded = recorder.load_trace("trace-1")
    assert loaded is not None
