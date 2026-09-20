"""Unit tests for TracedLLMProvider/TracedTestRunner — proving they
implement the wrapped interface transparently (same return value,
real exceptions still propagate) while recording a span.
"""

from typing import Optional

import pytest

from rag.llm.base import LLMProvider
from sandbox.base import TestRunner
from sandbox.exceptions import NoTestCommandError
from sandbox.models import TestRunResult

from observability.providers import TracedLLMProvider, TracedTestRunner
from observability.recorder import InMemoryRecorder
from observability.tracer import Tracer


class _FakeLLMProvider(LLMProvider):
    def __init__(self, response: str = "a response") -> None:
        self.response = response
        self.last_call = None

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        self.last_call = (prompt, system)
        return self.response


class _FailingLLMProvider(LLMProvider):
    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        raise RuntimeError("provider is down")


def test_traced_llm_provider_returns_the_real_response_unchanged():
    tracer = Tracer(InMemoryRecorder())
    wrapped = TracedLLMProvider(_FakeLLMProvider("hello world"), tracer)

    result = wrapped.generate("a prompt")

    assert result == "hello world"


def test_traced_llm_provider_passes_through_arguments_unchanged():
    fake = _FakeLLMProvider()
    tracer = Tracer(InMemoryRecorder())
    wrapped = TracedLLMProvider(fake, tracer)

    wrapped.generate("my prompt", system="my system")

    assert fake.last_call == ("my prompt", "my system")


def test_traced_llm_provider_records_a_span_with_safe_metadata():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    wrapped = TracedLLMProvider(_FakeLLMProvider("a response"), tracer)
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "outer", "node"):
        wrapped.generate("hello", system="sys")

    tracer.finish_trace("t1", "completed")
    trace = recorder.get_trace("t1")
    llm_span = next(s for s in trace.spans if s.kind == "llm")
    assert llm_span.attributes["llm_provider"] == "_FakeLLMProvider"
    assert llm_span.attributes["prompt_length"] == len("hello")
    assert llm_span.attributes["has_system_prompt"] is True
    assert llm_span.attributes["response_length"] == len("a response")
    # The prompt/response TEXT itself is never recorded, only lengths.
    assert "hello" not in str(llm_span.attributes)


def test_traced_llm_provider_does_not_record_the_prompt_or_response_content():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    secret_prompt = "the user's private source code goes here"
    wrapped = TracedLLMProvider(_FakeLLMProvider("also secret content"), tracer)
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "outer", "node"):
        wrapped.generate(secret_prompt)

    tracer.finish_trace("t1", "completed")
    trace = recorder.get_trace("t1")
    llm_span = next(s for s in trace.spans if s.kind == "llm")
    assert secret_prompt not in str(llm_span.attributes)
    assert "also secret content" not in str(llm_span.attributes)


def test_traced_llm_provider_propagates_real_exceptions():
    tracer = Tracer(InMemoryRecorder())
    wrapped = TracedLLMProvider(_FailingLLMProvider(), tracer)

    with pytest.raises(RuntimeError, match="provider is down"):
        wrapped.generate("prompt")


class _FakeTestRunner(TestRunner):
    def __init__(self, result: TestRunResult) -> None:
        self._result = result

    def run(self, root_path: str) -> TestRunResult:
        return self._result


def _make_result(passed: bool) -> TestRunResult:
    return TestRunResult(
        command=["pytest"], exit_code=0 if passed else 1, stdout="", stderr="", timed_out=False, duration_seconds=0.1
    )


def test_traced_test_runner_returns_the_real_result_unchanged():
    tracer = Tracer(InMemoryRecorder())
    result = _make_result(passed=True)
    wrapped = TracedTestRunner(_FakeTestRunner(result), tracer)

    assert wrapped.run("/some/path") is result


def test_traced_test_runner_records_a_span_with_test_outcome():
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    wrapped = TracedTestRunner(_FakeTestRunner(_make_result(passed=False)), tracer)
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "outer", "node"):
        wrapped.run("/some/path")

    tracer.finish_trace("t1", "completed")
    trace = recorder.get_trace("t1")
    sandbox_span = next(s for s in trace.spans if s.kind == "sandbox")
    assert sandbox_span.attributes["passed"] is False
    assert sandbox_span.attributes["exit_code"] == 1


class _FailingTestRunner(TestRunner):
    def run(self, root_path: str) -> TestRunResult:
        raise NoTestCommandError("no tests here")


def test_traced_test_runner_propagates_real_exceptions():
    tracer = Tracer(InMemoryRecorder())
    wrapped = TracedTestRunner(_FailingTestRunner(), tracer)

    with pytest.raises(NoTestCommandError):
        wrapped.run("/some/path")
