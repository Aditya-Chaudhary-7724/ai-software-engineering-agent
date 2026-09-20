"""Integration tests for the modification evaluation runner — real
PostgreSQL + Neo4j (+ real Docker for the failing-change case, skipped
honestly if unreachable), real ModificationService/AgentService.
"""

from evaluation.runners.modification import run_modification_evaluation

from tests.evaluation.conftest import neo4j_client, requires_neo4j, requires_postgres, vector_store


@requires_postgres
@requires_neo4j
def test_returns_the_three_expected_cases(vector_store, neo4j_client):
    results = run_modification_evaluation(vector_store, neo4j_client)

    assert {r.case_id for r in results} == {
        "modification-approval-and-scope",
        "modification-stale-change-protection",
        "modification-failed-change-reported-honestly",
    }
    assert all(r.category == "modification" for r in results)


@requires_postgres
@requires_neo4j
def test_all_non_skipped_cases_pass(vector_store, neo4j_client):
    results = run_modification_evaluation(vector_store, neo4j_client)

    for result in results:
        if result.skipped:
            continue
        assert result.passed, f"{result.case_id} failed: {[m for m in result.metrics if m.passed is False]}"
