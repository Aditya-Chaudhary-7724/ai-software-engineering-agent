"""Optional LangSmith exporter.

**Whether LangSmith/Langfuse add real value here, evaluated rather
than assumed:** this project's agent is already built on LangGraph,
and LangSmith has first-class LangGraph tracing support plus the
`langsmith` package was already an installed transitive dependency
(via `langchain-core`) before this phase touched anything — making it
the lower-cost, better-architectural-fit option of the two if external
tracing is ever wanted. Langfuse would require adding an entirely new
dependency for no additional benefit to this specific stack. That
said: this is a single-developer, local project. The actual,
load-bearing requirement — inspecting one agent run end-to-end — is
already fully met by the local `JSONFileRecorder` +
`backend/scripts/inspect_trace.py`, with no hosted account, network
call, or team-collaboration need. So this exporter exists as a
genuine, working, OPTIONAL adapter: not wired in by default anywhere
in this codebase, and not exercised against a live LangSmith account in
this environment (no `LANGSMITH_API_KEY` is configured, and this
project's own instructions say never to request one). Its request-
shaping logic IS unit-tested against a mocked `langsmith.Client` — the
same "mechanism proven, live behavior requires real credentials"
pattern already used for `OpenAIEmbeddingProvider`/`AnthropicLLMProvider`
elsewhere in this project. Do not read a passing test here as proof
that a live LangSmith project actually receives these traces.
"""

import os
from datetime import datetime
from typing import Literal, Optional

from langsmith import Client

from observability.exceptions import MissingCredentialsError
from observability.exporters.base import TraceExporter
from observability.models import Span, Trace

_RunType = Literal["tool", "chain", "llm", "retriever", "embedding", "prompt", "parser"]


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None

_RUN_TYPE_BY_KIND: dict = {
    "llm": "llm",
    "retrieval": "retriever",
    "graph": "retriever",
    "tool": "tool",
}
_DEFAULT_RUN_TYPE: _RunType = "chain"


class LangSmithExporter(TraceExporter):
    def __init__(self, client: Optional[Client] = None, project_name: Optional[str] = None) -> None:
        if client is None:
            if not (os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")):
                raise MissingCredentialsError(
                    "LANGSMITH_API_KEY (or LANGCHAIN_API_KEY) is not set. Add a real LangSmith API "
                    "key to your local .env to use LangSmithExporter; it is never read from source code."
                )
            client = Client()
        self._client = client
        self._project_name = project_name or os.environ.get("LANGSMITH_PROJECT", "ai-swe-agent")

    def export(self, trace: Trace) -> None:
        self._client.create_run(
            id=trace.trace_id,
            name=trace.name,
            run_type=_DEFAULT_RUN_TYPE,
            inputs=trace.attributes,
            project_name=self._project_name,
            start_time=_parse_iso(trace.started_at),
        )
        for span in trace.spans:
            self._export_span(span)
        self._client.update_run(
            trace.trace_id,
            outputs={"status": trace.status},
            end_time=_parse_iso(trace.ended_at),
        )

    def _export_span(self, span: Span) -> None:
        self._client.create_run(
            id=span.span_id,
            name=span.name,
            run_type=_RUN_TYPE_BY_KIND.get(span.kind, _DEFAULT_RUN_TYPE),
            inputs=span.attributes,
            parent_run_id=span.parent_span_id,
            trace_id=span.trace_id,
            project_name=self._project_name,
            start_time=_parse_iso(span.start_time),
        )
        self._client.update_run(
            span.span_id,
            outputs={"status": span.status},
            error=span.error,
            end_time=_parse_iso(span.end_time),
        )
