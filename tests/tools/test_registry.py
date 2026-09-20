from pydantic import BaseModel

from observability.recorder import InMemoryRecorder
from observability.tracer import Tracer

from tools.exceptions import ToolAuthorizationError, ToolInputError, ToolNotAvailableError
from tools.registry import Tool, ToolRegistry


class _EchoInput(BaseModel):
    value: int


class _EchoOutput(BaseModel):
    doubled: int


def _echo_handler(input_data: _EchoInput) -> _EchoOutput:
    return _EchoOutput(doubled=input_data.value * 2)


def _failing_handler(input_data: _EchoInput) -> _EchoOutput:
    if input_data.value < 0:
        raise ToolInputError("value must be non-negative")
    if input_data.value == 0:
        raise ToolAuthorizationError("zero is not authorized")
    raise ToolNotAvailableError("not available for this value")


def _build_registry(handler=_echo_handler) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        Tool(name="echo", description="doubles a value", input_model=_EchoInput, output_model=_EchoOutput, handler=handler)
    )
    return registry


def test_invoke_unknown_tool_returns_error():
    registry = _build_registry()
    result = registry.invoke("does_not_exist", {})

    assert result.success is False
    assert "Unknown tool" in result.error


def test_invoke_valid_input_returns_success():
    registry = _build_registry()
    result = registry.invoke("echo", {"value": 5})

    assert result.success is True
    assert result.data == {"doubled": 10}


def test_invoke_invalid_input_returns_validation_error():
    registry = _build_registry()
    result = registry.invoke("echo", {"value": "not-a-number"})

    assert result.success is False
    assert "Invalid input" in result.error


def test_invoke_missing_required_field_returns_validation_error():
    registry = _build_registry()
    result = registry.invoke("echo", {})

    assert result.success is False
    assert "Invalid input" in result.error


def test_invoke_maps_tool_input_error_to_result():
    registry = _build_registry(handler=_failing_handler)
    result = registry.invoke("echo", {"value": -1})

    assert result.success is False
    assert "non-negative" in result.error


def test_invoke_maps_authorization_error_to_result():
    registry = _build_registry(handler=_failing_handler)
    result = registry.invoke("echo", {"value": 0})

    assert result.success is False
    assert "not authorized" in result.error


def test_invoke_maps_not_available_error_to_result():
    registry = _build_registry(handler=_failing_handler)
    result = registry.invoke("echo", {"value": 1})

    assert result.success is False
    assert "not available" in result.error


def test_list_tools_returns_registered_tools():
    registry = _build_registry()
    tools = registry.list_tools()

    assert len(tools) == 1
    assert tools[0].name == "echo"


def test_get_returns_none_for_unknown_tool():
    registry = _build_registry()
    assert registry.get("nope") is None


# Phase 13: ToolRegistry is ready to be traced the moment a caller
# routes through it, even though the current agent graph doesn't (see
# docs/architecture.md's "Evaluation" section on how "tool selection"
# is honestly scoped). Behavior without a tracer (all tests above) is
# unaffected — these tests cover the opt-in tracing path specifically.


def test_invoke_without_a_tracer_behaves_exactly_as_before():
    registry = ToolRegistry()  # no tracer — the pre-Phase-13 constructor call
    result = registry.invoke("does_not_exist", {})
    assert result.success is False


def test_invoke_with_a_tracer_records_a_tool_span():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    registry = ToolRegistry(tracer=tracer)
    registry.register(
        Tool(name="echo", description="doubles a value", input_model=_EchoInput, output_model=_EchoOutput, handler=_echo_handler)
    )
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "outer", "node"):
        result = registry.invoke("echo", {"value": 5})

    tracer.finish_trace("t1", "completed")
    assert result.success is True
    tool_span = next(s for s in recorder.get_trace("t1").spans if s.kind == "tool")
    assert tool_span.attributes["tool_name"] == "echo"
    assert tool_span.attributes["argument_keys"] == ["value"]
    assert tool_span.attributes["success"] is True


def test_invoke_with_a_tracer_records_failure_without_leaking_argument_values():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    registry = ToolRegistry(tracer=tracer)
    registry.register(
        Tool(name="echo", description="doubles a value", input_model=_EchoInput, output_model=_EchoOutput, handler=_echo_handler)
    )
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "outer", "node"):
        registry.invoke("echo", {"value": "not-a-number"})

    tracer.finish_trace("t1", "completed")
    tool_span = next(s for s in recorder.get_trace("t1").spans if s.kind == "tool")
    assert tool_span.attributes["success"] is False
    assert "tool_error" in tool_span.attributes
    # Only the argument KEY is ever recorded, never the value.
    assert "not-a-number" not in str(tool_span.attributes["argument_keys"])
