"""Integration tests for the retrieval evaluation runner — real
PostgreSQL, real DeterministicLocalEmbeddingProvider, real
IngestionService/ParsingService/IndexingService (none reimplemented).
Neo4j is used when reachable for the graph-augmented hybrid method;
skipped honestly (not failed) otherwise.
"""

from evaluation.dataset import RETRIEVAL_CASES
from evaluation.runners.retrieval import run_retrieval_evaluation

from tests.evaluation.conftest import neo4j_client, requires_neo4j, requires_postgres, vector_store


@requires_postgres
def test_returns_one_result_per_case(vector_store):
    results = run_retrieval_evaluation(vector_store)

    assert {r.case_id for r in results} == {c.case_id for c in RETRIEVAL_CASES}
    assert all(r.category == "retrieval" for r in results)


@requires_postgres
def test_keyword_retrieval_finds_the_expected_files(vector_store):
    """Keyword search doesn't depend on embeddings, so this is the
    method expected to be reliably correct even with
    DeterministicLocalEmbeddingProvider — see retrieval.py's own
    docstring for why vector-only metrics are informational, not gating.
    """
    results = run_retrieval_evaluation(vector_store)

    for result in results:
        hit_rate_metric = next(m for m in result.metrics if m.name == "keyword_hit_rate")
        assert hit_rate_metric.value == 1.0
        assert hit_rate_metric.passed is True


@requires_postgres
def test_vector_metrics_are_informational_not_gating(vector_store):
    results = run_retrieval_evaluation(vector_store)

    for result in results:
        vector_hit_rate = next(m for m in result.metrics if m.name == "vector_hit_rate")
        assert vector_hit_rate.passed is None  # never gates pass/fail on the non-semantic stub


@requires_postgres
def test_cleans_up_the_repository_it_created(vector_store):
    run_retrieval_evaluation(vector_store)

    conn = vector_store.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM repositories")
            count_after = cur.fetchone()[0]
    finally:
        conn.close()

    assert count_after == 0


@requires_postgres
@requires_neo4j
def test_includes_graph_hybrid_metrics_when_neo4j_is_reachable(vector_store, neo4j_client):
    results = run_retrieval_evaluation(vector_store, neo4j_client)

    for result in results:
        assert any(m.name.startswith("graph_hybrid_") for m in result.metrics)
