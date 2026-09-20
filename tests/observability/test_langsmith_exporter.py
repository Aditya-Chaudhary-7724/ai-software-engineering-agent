"""Unit tests for LangSmithExporter with a MOCKED `langsmith.Client` —
no real network call, no live LangSmith account involved. Verifies the
request-shaping logic only. See langsmith_exporter.py's own docstring:
a passing test here is NOT proof that a live LangSmith project actually
receives these traces — that would require real LANGSMITH_API_KEY
credentials, which this environment intentionally does not have.
"""

import pytest

from observability.exceptions import MissingCredentialsError
from observability.exporters.langsmith_exporter import LangSmithExporter
from observability.models import Span, Trace


class _FakeLangSmithClient:
    def __init__(self):
        self.created_runs = []
        self.updated_runs = []

    def create_run(self, **kwargs):
        self.created_runs.append(kwargs)

    def update_run(self, run_id, **kwargs):
        self.updated_runs.append({"run_id": run_id, **kwargs})


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
        attributes={"task_type": "answer"},
    )
    return Trace(
        trace_id="trace-1",
        name="agent_thread",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:01+00:00",
        status="completed",
        attributes={"question": "hello"},
        spans=[span],
    )


def test_requires_credentials_when_no_client_or_key_given(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)

    with pytest.raises(MissingCredentialsError):
        LangSmithExporter()


def test_accepts_an_explicitly_injected_client_without_credentials(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)

    exporter = LangSmithExporter(client=_FakeLangSmithClient())  # must not raise

    assert exporter is not None


def test_export_creates_a_run_for_the_trace_and_each_span():
    fake_client = _FakeLangSmithClient()
    exporter = LangSmithExporter(client=fake_client, project_name="test-project")

    exporter.export(_sample_trace())

    assert len(fake_client.created_runs) == 2  # one for the trace, one for its span
    trace_run = fake_client.created_runs[0]
    assert trace_run["id"] == "trace-1"
    assert trace_run["name"] == "agent_thread"
    assert trace_run["project_name"] == "test-project"

    span_run = fake_client.created_runs[1]
    assert span_run["id"] == "span-1"
    assert span_run["parent_run_id"] is None
    assert span_run["run_type"] == "chain"  # kind "node" has no special mapping


def test_export_maps_span_kind_to_langsmith_run_type():
    fake_client = _FakeLangSmithClient()
    exporter = LangSmithExporter(client=fake_client)
    trace = _sample_trace()
    trace.spans[0].kind = "llm"

    exporter.export(trace)

    span_run = fake_client.created_runs[1]
    assert span_run["run_type"] == "llm"


def test_export_updates_the_trace_and_span_with_final_status():
    fake_client = _FakeLangSmithClient()
    exporter = LangSmithExporter(client=fake_client)

    exporter.export(_sample_trace())

    trace_update = fake_client.updated_runs[0]
    assert trace_update["run_id"] == "span-1"  # span updated first (in the loop), then the trace
    span_update = next(u for u in fake_client.updated_runs if u["run_id"] == "trace-1")
    assert span_update["outputs"] == {"status": "completed"}


def test_export_does_not_leak_secrets_from_trace_attributes():
    """The trace's own attributes are passed through as-is to `inputs`
    here — this exporter relies on the Tracer having already sanitized
    them before they ever reach a Trace object (see tracer.py's `span`/
    `start_trace`), not on re-sanitizing itself. This test documents
    that assumption: an already-sanitized trace stays sanitized through
    export.
    """
    fake_client = _FakeLangSmithClient()
    exporter = LangSmithExporter(client=fake_client)
    trace = _sample_trace()
    trace.attributes["api_key"] = "***REDACTED***"  # as the Tracer would have already left it

    exporter.export(trace)

    assert fake_client.created_runs[0]["inputs"]["api_key"] == "***REDACTED***"
