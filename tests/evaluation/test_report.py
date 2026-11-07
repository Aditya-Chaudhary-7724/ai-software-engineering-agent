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


def test_report_separates_deterministic_and_real_model_counts():
    from evaluation.models import KIND_REAL_MODEL

    report = EvaluationReport(
        results=[
            CaseResult(case_id="d1", category="answers", description="d", passed=True),
            CaseResult(case_id="d2", category="answers", description="d", passed=False),
            CaseResult(case_id="r1", category="answers_real_model", description="r", passed=False, skipped=True,
                       skip_reason="no key", evaluation_kind=KIND_REAL_MODEL),
        ],
        environment={"llm_provider": "StubLLMProvider (placeholder, deterministic)"},
    )
    text = render_text_report(report)
    assert "Deterministic: 1 passed, 1 failed, 0 skipped (of 2)" in text
    assert "Real-model: 0 passed, 0 failed, 1 skipped (of 1)" in text
    assert "no real-model evaluation ran" in text
    assert "llm_provider: StubLLMProvider" in text


def test_real_model_results_are_labelled_and_suppress_the_no_real_model_note():
    from evaluation.models import KIND_REAL_MODEL

    report = EvaluationReport(
        results=[CaseResult(case_id="r1", category="answers_real_model", description="r", passed=True, evaluation_kind=KIND_REAL_MODEL)]
    )
    text = render_text_report(report)
    assert "[PASS · REAL MODEL] r1" in text
    assert "no real-model evaluation ran" not in text


def test_export_json_includes_summary_and_environment(tmp_path):
    import json

    report = EvaluationReport(
        results=[CaseResult(case_id="d1", category="answers", description="d", passed=True)], environment={"neo4j": "reachable"}
    )
    path = tmp_path / "r.json"
    export_json(report, path)
    data = json.loads(path.read_text())
    assert data["summary"]["deterministic"] == {"total": 1, "passed": 1, "failed": 0, "skipped": 0}
    assert data["summary"]["real_model"]["total"] == 0
    assert data["environment"] == {"neo4j": "reachable"}
    assert data["results"][0]["evaluation_kind"] == "deterministic"


def test_database_label_never_includes_credentials():
    from evaluation.environment import database_label

    label = database_label("postgresql://alice:s3cret@db.internal:5432/app?sslmode=require")
    assert label == "db.internal/app"
    assert "s3cret" not in label and "alice" not in label
