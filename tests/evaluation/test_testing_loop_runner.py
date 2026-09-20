"""Integration tests for the testing-loop evaluation runner — real
PostgreSQL + Neo4j, real AgentService/LangGraph workflow, with
ScriptedTestRunner standing in for Docker (see that module's docstring
for why) so the loop's routing/bounding logic is exercised
deterministically.
"""

from evaluation.runners.testing_loop import run_testing_loop_evaluation

from tests.evaluation.conftest import neo4j_client, requires_neo4j, requires_postgres, vector_store


@requires_postgres
@requires_neo4j
def test_returns_the_five_expected_cases(vector_store, neo4j_client):
    results = run_testing_loop_evaluation(vector_store, neo4j_client)

    assert {r.case_id for r in results} == {
        "testing-loop-first-pass-success",
        "testing-loop-fail-then-recover",
        "testing-loop-iteration-limit",
        "testing-loop-wall-clock-timeout",
        "testing-loop-approval-not-bypassable",
    }
    assert all(r.category == "testing_loop" for r in results)


@requires_postgres
@requires_neo4j
def test_all_cases_pass(vector_store, neo4j_client):
    results = run_testing_loop_evaluation(vector_store, neo4j_client)

    for result in results:
        assert result.passed, f"{result.case_id} failed: {[m for m in result.metrics if m.passed is False]}"


@requires_postgres
@requires_neo4j
def test_wall_clock_timeout_case_restores_the_original_budget_afterward(vector_store, neo4j_client):
    """The runner temporarily patches agent.nodes.MAX_LOOP_SECONDS to 0
    for one case — this proves it's restored afterward, so it can never
    leak into a later evaluation case or test.
    """
    import agent.nodes as agent_nodes
    from agent.state import MAX_LOOP_SECONDS as ORIGINAL_MAX_LOOP_SECONDS

    run_testing_loop_evaluation(vector_store, neo4j_client)

    assert agent_nodes.MAX_LOOP_SECONDS == ORIGINAL_MAX_LOOP_SECONDS
