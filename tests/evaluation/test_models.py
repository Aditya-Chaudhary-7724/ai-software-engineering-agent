"""Pure unit tests for EvaluationReport's aggregation properties, with
hand-built CaseResult objects — no service involved.
"""

from evaluation.models import CaseResult, EvaluationReport, MetricResult


def _result(case_id, category, passed, skipped=False):
    return CaseResult(case_id=case_id, category=category, description="d", passed=passed, skipped=skipped)


def test_totals_and_counts():
    report = EvaluationReport(
        results=[
            _result("a", "retrieval", True),
            _result("b", "retrieval", False),
            _result("c", "agent", True),
            _result("d", "agent", False, skipped=True),
        ]
    )

    assert report.total == 4
    assert report.passed_count == 2
    assert report.failed_count == 1
    assert report.skipped_count == 1


def test_by_category_groups_correctly():
    report = EvaluationReport(
        results=[
            _result("a", "retrieval", True),
            _result("b", "agent", True),
            _result("c", "retrieval", False),
        ]
    )

    grouped = report.by_category()

    assert {r.case_id for r in grouped["retrieval"]} == {"a", "c"}
    assert {r.case_id for r in grouped["agent"]} == {"b"}


def test_skipped_results_excluded_from_passed_failed_counts():
    report = EvaluationReport(results=[_result("a", "retrieval", False, skipped=True)])

    assert report.passed_count == 0
    assert report.failed_count == 0
    assert report.skipped_count == 1


def test_metric_result_defaults_are_informational():
    metric = MetricResult(name="m", value=0.5)
    assert metric.passed is None
    assert metric.expected is None
