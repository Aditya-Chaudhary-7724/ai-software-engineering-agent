"""Unit tests for the `traced_node` decorator used to wire tracing into
`agent/graph.py` without touching `agent/nodes.py`'s own function
bodies.
"""

from observability.node_tracing import traced_node
from observability.recorder import InMemoryRecorder
from observability.tracer import Tracer


def _sample_node(state):
    return {"task_type": state.get("question", "").split()[0] if state.get("question") else None, "extra": "value"}


def test_wrapped_node_returns_the_same_result_as_the_original():
    tracer = Tracer(InMemoryRecorder())
    wrapped = traced_node(tracer, "sample", "node")(_sample_node)

    result = wrapped({"question": "hello world"})

    assert result == {"task_type": "hello", "extra": "value"}


def test_wrapped_node_records_a_span_with_the_given_name_and_kind():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    wrapped = traced_node(tracer, "sample", "retrieval")(_sample_node)
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "agent_run", "agent"):
        wrapped({"question": "hi"})

    tracer.finish_trace("t1", "completed")
    span = next(s for s in recorder.get_trace("t1").spans if s.name == "sample")
    assert span.kind == "retrieval"
    assert span.status == "ok"


def test_extract_callback_adds_attributes_from_the_node_result():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    wrapped = traced_node(tracer, "sample", "node", extract=lambda r: {"task_type": r["task_type"]})(_sample_node)
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "agent_run", "agent"):
        wrapped({"question": "modify the code"})

    tracer.finish_trace("t1", "completed")
    span = next(s for s in recorder.get_trace("t1").spans if s.name == "sample")
    assert span.attributes["task_type"] == "modify"


def test_a_broken_extract_callback_does_not_break_the_node():
    tracer = Tracer(InMemoryRecorder())

    def broken_extract(result):
        raise RuntimeError("extractor bug")

    wrapped = traced_node(tracer, "sample", "node", extract=broken_extract)(_sample_node)

    result = wrapped({"question": "hello"})

    assert result == {"task_type": "hello", "extra": "value"}  # node still ran and returned correctly


def test_node_exception_still_propagates_and_marks_span_as_error():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)

    def failing_node(state):
        raise ValueError("node failed")

    wrapped = traced_node(tracer, "sample", "node")(failing_node)
    tracer.start_trace("t1", "agent_thread")

    try:
        with tracer.span("t1", "agent_run", "agent"):
            wrapped({})
    except ValueError:
        pass

    tracer.finish_trace("t1", "failed")
    span = next(s for s in recorder.get_trace("t1").spans if s.name == "sample")
    assert span.status == "error"
