"""Pure unit tests for report rendering/export — hand-built results,
no service involved.
"""

import json

from evaluation.models import CaseResult, EvaluationReport, MetricResult
from evaluation.report import export_json, render_text_report


def _sample_report() -> EvaluationReport:
    return EvaluationReport(
        results=[
            CaseResult(
                case_id="retrieval-example",
                category="retrieval",
                description="An example retrieval case.",
                passed=True,
                metrics=[
                    MetricResult(name="keyword_recall@5", value=1.0, expected=1.0, passed=True),
                    MetricResult(name="keyword_reciprocal_rank", value=1.0),
                ],
                latency_seconds=0.01,
                retrieval_results=["a.py"],
            ),
            CaseResult(
                case_id="agent-example",
                category="agent",
                description="An example agent case.",
                passed=False,
                metrics=[MetricResult(name="routing_correct", value=0.0, expected=1.0, passed=False)],
                failure_reason="routed incorrectly",
            ),
            CaseResult(
                case_id="testing-loop-example",
                category="testing_loop",
                description="An example skipped case.",
                passed=False,
                skipped=True,
                skip_reason="Docker not reachable (REQUIRES EXTERNAL SERVICE)",
            ),
        ]
    )


def test_render_text_report_includes_summary_counts():
    text = render_text_report(_sample_report())

    assert "Total cases: 3" in text
    assert "Passed: 1" in text
    assert "Failed: 1" in text
    assert "Skipped: 1" in text


def test_render_text_report_shows_pass_fail_skip_per_case():
    text = render_text_report(_sample_report())

    assert "[PASS] retrieval-example" in text
    assert "[FAIL] agent-example" in text
    assert "[SKIP] testing-loop-example: Docker not reachable" in text
    assert "routed incorrectly" in text


def test_render_text_report_shows_metric_values_and_gates():
    text = render_text_report(_sample_report())

    assert "keyword_recall@5: 1.000 (expected 1.0) [PASS]" in text
    # An informational (non-gating) metric shows a value but no [PASS]/[FAIL] tag.
    assert "keyword_reciprocal_rank: 1.000" in text
    assert "keyword_reciprocal_rank: 1.000 [PASS]" not in text


def test_render_text_report_aggregates_mrr_for_retrieval_only():
    text = render_text_report(_sample_report())

    assert "MRR (keyword): 1.000" in text


def test_export_json_round_trips_all_results(tmp_path):
    report = _sample_report()
    out_path = tmp_path / "report.json"

    export_json(report, out_path)
    data = json.loads(out_path.read_text())

    assert len(data["results"]) == 3
    assert data["results"][0]["case_id"] == "retrieval-example"
    assert data["results"][1]["passed"] is False
    assert data["results"][2]["skipped"] is True
