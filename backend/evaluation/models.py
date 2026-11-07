"""Structured result models for the evaluation framework.

Plain, dependency-free dataclasses — same rationale as every other
phase: no API boundary exists yet to justify a validation library, and
these are produced/consumed entirely within this backend.

A `CaseResult` is the atomic unit a report is built from: one
evaluation case, the metrics computed for it, and whether it passed —
always with enough detail (`failure_reason`, `retrieval_results`,
`execution_log`) to explain *why*, not just pass/fail.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# F7: what produced a case's result. "deterministic" cases use only fixed
# inputs and non-production stand-ins (StubLLMProvider, hash embeddings,
# scripted test runners) and must give the same answer on every run;
# "real_model" cases need a real LLM/embedding provider, are skipped when
# none is configured, and are never added to the deterministic totals.
KIND_DETERMINISTIC = "deterministic"
KIND_REAL_MODEL = "real_model"


@dataclass(frozen=True)
class MetricResult:
    """One measured value for one case.

    `passed` is `None` for a purely informational metric (reported but
    not itself a pass/fail gate — e.g. a raw retrieval count); it is
    `True`/`False` when this metric is one of the case's pass/fail
    criteria. A case can carry several metrics with only some of them
    gating its overall `CaseResult.passed`.
    """

    name: str
    value: float
    expected: Optional[float] = None
    passed: Optional[bool] = None


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    category: str  # "retrieval" | "rag" | "agent" | "modification" | "testing_loop"
    description: str
    passed: bool
    metrics: List[MetricResult] = field(default_factory=list)
    latency_seconds: Optional[float] = None
    # Execution-log entries or tool/service invocations observed while
    # running this case, where relevant (e.g. agent cases) — the same
    # safe-summary strings the agent itself produces, never raw model
    # reasoning.
    execution_log: List[str] = field(default_factory=list)
    # relative_paths actually retrieved, where relevant (retrieval/RAG cases).
    retrieval_results: List[str] = field(default_factory=list)
    failure_reason: Optional[str] = None
    skipped: bool = False
    skip_reason: Optional[str] = None
    # Phase 13 connection: set to the AgentService thread_id (== the
    # observability trace_id — see agent/service.py) when this case ran
    # a real agent, so a failing case can be inspected end-to-end with
    # `backend/scripts/inspect_trace.py <trace_id>`. None for cases that
    # don't run the agent (pure retrieval/RAG cases) or when tracing
    # wasn't wired in for a given run.
    trace_id: Optional[str] = None
    evaluation_kind: str = KIND_DETERMINISTIC


@dataclass(frozen=True)
class EvaluationReport:
    results: List[CaseResult]
    # F7: the configuration the run actually used (provider names, which
    # services were reachable) — never a credential or a full URL.
    environment: Dict[str, str] = field(default_factory=dict)

    def of_kind(self, kind: str) -> List[CaseResult]:
        return [r for r in self.results if r.evaluation_kind == kind]

    def counts(self, kind: Optional[str] = None) -> Dict[str, int]:
        results = self.results if kind is None else self.of_kind(kind)
        evaluated = [r for r in results if not r.skipped]
        return {
            "total": len(results),
            "passed": sum(1 for r in evaluated if r.passed),
            "failed": sum(1 for r in evaluated if not r.passed),
            "skipped": len(results) - len(evaluated),
        }

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def evaluated(self) -> List[CaseResult]:
        return [r for r in self.results if not r.skipped]

    @property
    def skipped(self) -> List[CaseResult]:
        return [r for r in self.results if r.skipped]

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.evaluated if r.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.evaluated if not r.passed)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    def by_category(self) -> Dict[str, List[CaseResult]]:
        grouped: Dict[str, List[CaseResult]] = {}
        for result in self.results:
            grouped.setdefault(result.category, []).append(result)
        return grouped
