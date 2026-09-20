"""Renders an `EvaluationReport` as human-readable text, and optionally
exports it as JSON.

No new database: a JSON file on disk is sufficient for "persist a run
so it can be diffed against a later one" — there's no query, index, or
concurrent-write need that would justify Postgres/another store for
what is, honestly, a handful of small reports (this project's own
"do not introduce another database unless there is a real
architectural reason" policy, applied to Phase 12).
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Union

from evaluation.models import EvaluationReport


def _aggregate_mrr_lines(report: EvaluationReport) -> list:
    """Aggregates each retrieval method's per-case `*_reciprocal_rank`
    metric (already computed by `evaluation.metrics.reciprocal_rank` in
    the retrieval runner) into one MRR figure per method, purely for the
    summary — the per-case values remain visible in the detailed
    section below. Averages raw floats directly rather than calling
    `evaluation.metrics.mean_reciprocal_rank`, since that function takes
    (retrieved, relevant) pairs, not already-computed scores.
    """
    by_method: dict = {}
    for result in report.by_category().get("retrieval", []):
        for metric in result.metrics:
            if metric.name.endswith("_reciprocal_rank"):
                method = metric.name[: -len("_reciprocal_rank")]
                by_method.setdefault(method, []).append(metric.value)

    lines = []
    for method, values in sorted(by_method.items()):
        mrr = sum(values) / len(values) if values else 0.0
        lines.append(f"    MRR ({method}): {mrr:.3f}")
    return lines


def render_text_report(report: EvaluationReport) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("AI SOFTWARE ENGINEERING AGENT — EVALUATION REPORT")
    lines.append("=" * 70)
    lines.append(
        f"Total cases: {report.total}  |  Passed: {report.passed_count}  |  "
        f"Failed: {report.failed_count}  |  Skipped: {report.skipped_count}"
    )
    lines.append("")

    for category, results in report.by_category().items():
        evaluated = [r for r in results if not r.skipped]
        passed = sum(1 for r in evaluated if r.passed)
        lines.append(f"--- {category.upper()} ({passed}/{len(evaluated)} passed"
                     f"{f', {len(results) - len(evaluated)} skipped' if len(results) != len(evaluated) else ''}) ---")

        if category == "retrieval":
            lines.extend(_aggregate_mrr_lines(report))

        for result in results:
            if result.skipped:
                lines.append(f"  [SKIP] {result.case_id}: {result.skip_reason}")
                continue

            status = "PASS" if result.passed else "FAIL"
            latency = f" ({result.latency_seconds:.3f}s)" if result.latency_seconds is not None else ""
            lines.append(f"  [{status}] {result.case_id}{latency} — {result.description}")

            for metric in result.metrics:
                gate = ""
                if metric.passed is not None:
                    gate = " [PASS]" if metric.passed else " [FAIL]"
                expected = f" (expected {metric.expected})" if metric.expected is not None else ""
                lines.append(f"      {metric.name}: {metric.value:.3f}{expected}{gate}")

            if result.retrieval_results:
                lines.append(f"      retrieved: {result.retrieval_results}")
            if result.failure_reason:
                lines.append(f"      failure_reason: {result.failure_reason}")
            if result.trace_id:
                lines.append(
                    f"      trace_id: {result.trace_id}  "
                    f"(inspect: .venv/bin/python backend/scripts/inspect_trace.py {result.trace_id})"
                )

        lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)


def export_json(report: EvaluationReport, path: Union[str, Path]) -> None:
    data = {"results": [asdict(result) for result in report.results]}
    Path(path).write_text(json.dumps(data, indent=2))
