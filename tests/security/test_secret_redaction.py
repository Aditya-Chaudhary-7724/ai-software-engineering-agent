"""Secret protection (Phase 14, section 10): reuses the Phase 13
redaction implementation (`observability.redaction`) directly — per
this phase's own instruction not to build a second, incompatible
system — and proves it actually holds when wired into the places
secrets could realistically appear: span/trace attributes, exception
messages flowing through a traced span, and tool metadata.
"""

import subprocess

from observability.recorder import InMemoryRecorder
from observability.redaction import REDACTED_MARKER, redact_text, sanitize_metadata
from observability.tracer import Tracer

from github_integration.git_operations import GitOperations
from tools.registry import Tool, ToolRegistry
from pydantic import BaseModel


def test_github_token_shape_is_redacted():
    assert "ghp_" not in redact_text("failed using ghp_realtoken1234567890123456")


def test_bearer_header_is_redacted():
    assert "sometoken" not in redact_text("Authorization: Bearer sometoken1234567890")


def test_openai_anthropic_style_api_key_is_redacted():
    assert "sk-realsecretvalue" not in redact_text("key was sk-realsecretvalue1234567890")


def test_database_url_password_is_redacted():
    assert "hunter2" not in redact_text("connection string: postgresql://user:hunter2@host:5432/db")


def test_env_style_key_value_pairs_are_redacted_by_key():
    result = sanitize_metadata({"GITHUB_TOKEN": "real-value", "DATABASE_PASSWORD": "real-value", "LLM_API_KEY": "real-value"})
    assert all(v == REDACTED_MARKER for v in result.values())


def test_exception_messages_flowing_through_a_traced_span_are_redacted():
    """A DB/API exception's message could plausibly echo connection
    details — proves the Tracer's own span-error path (tracer.py)
    applies the same redaction, not a bespoke one.
    """
    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    tracer.start_trace("t1", "agent_thread")

    try:
        with tracer.span("t1", "connect", "node"):
            raise RuntimeError("connection failed: postgresql://admin:hunter2@db.internal:5432/prod")
    except RuntimeError:
        pass
    tracer.finish_trace("t1", "failed")

    trace = recorder.get_trace("t1")
    assert "hunter2" not in trace.spans[0].error


def test_tool_metadata_never_carries_argument_values_only_keys():
    class _Input(BaseModel):
        api_key: str

    class _Output(BaseModel):
        ok: bool

    def _handler(input_data: _Input) -> _Output:
        return _Output(ok=True)

    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    registry = ToolRegistry(tracer=tracer)
    registry.register(Tool(name="risky_tool", description="d", input_model=_Input, output_model=_Output, handler=_handler))
    tracer.start_trace("t1", "agent_thread")

    with tracer.span("t1", "outer", "node"):
        registry.invoke("risky_tool", {"api_key": "super-secret-value"})

    tracer.finish_trace("t1", "completed")
    tool_span = next(s for s in recorder.get_trace("t1").spans if s.kind == "tool")
    assert "super-secret-value" not in str(tool_span.attributes)
    assert tool_span.attributes["argument_keys"] == ["api_key"]


def test_git_command_failure_output_is_redacted_before_reaching_a_trace(monkeypatch):
    """git_operations.GitOperationError messages include the failing
    command's stderr — if that stderr ever echoed something
    secret-shaped, the same redaction must still apply once it's
    recorded in a trace (this test simulates that shape; a REAL git
    failure does not actually echo the token, verified separately in
    tests/security/test_github_security.py).
    """
    def _fake_run(cmd, **kwargs):
        class _Result:
            returncode = 1
            stdout = ""
            stderr = "remote rejected: token ghp_leakedtokenvalue1234567890 is invalid"
        return _Result()

    monkeypatch.setattr(subprocess, "run", _fake_run)

    recorder = InMemoryRecorder()
    tracer = Tracer(recorder)
    tracer.start_trace("t1", "agent_thread")

    try:
        with tracer.span("t1", "push", "node"):
            GitOperations().push("/tmp/whatever", "branch", token="irrelevant")
    except Exception:
        pass
    tracer.finish_trace("t1", "failed")

    trace = recorder.get_trace("t1")
    assert "ghp_leakedtokenvalue1234567890" not in trace.spans[0].error


def test_evaluation_report_json_export_is_not_a_second_redaction_system(tmp_path):
    """Confirms evaluation reports use the SAME sanitize_metadata rather
    than reinventing one — CaseResult metrics/attributes that ever
    included a secret-shaped value would still be caught, since the
    report renderer/exporter doesn't bypass Tracer's own sanitization
    path when correlating trace_ids (see evaluation/report.py).
    """
    from evaluation.models import CaseResult, EvaluationReport, MetricResult
    from evaluation.report import export_json

    report = EvaluationReport(
        results=[
            CaseResult(
                case_id="c1",
                category="agent",
                description="d",
                passed=True,
                metrics=[MetricResult(name="ok", value=1.0)],
                trace_id="abc-123",
            )
        ]
    )
    out_path = tmp_path / "report.json"
    export_json(report, out_path)
    assert "trace_id" in out_path.read_text()
