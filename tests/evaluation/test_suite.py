"""Unit tests for run_full_suite's skip behavior, with
postgres_available/neo4j_available monkeypatched — proving the suite
degrades to explicit skipped CaseResults (never a crash, never a
silently-dropped category) when a service isn't reachable. Real,
end-to-end suite behavior against live services is exercised by
`backend/scripts/run_evaluation.py` and each category's own runner
tests.
"""

import evaluation.suite as suite_module


def test_all_categories_skipped_when_postgres_unreachable(monkeypatch):
    monkeypatch.setattr(suite_module, "postgres_available", lambda url: False)

    report = suite_module.run_full_suite(database_url="postgresql://irrelevant")

    assert report.total == 5
    assert all(r.skipped for r in report.results)
    assert {r.category for r in report.results} == {"retrieval", "rag", "agent", "modification", "testing_loop"}
    assert all("PostgreSQL not reachable" in (r.skip_reason or "") for r in report.results)


def test_agent_modification_testing_loop_skipped_when_neo4j_unreachable(monkeypatch):
    monkeypatch.setattr(suite_module, "postgres_available", lambda url: True)
    monkeypatch.setattr(suite_module, "neo4j_available", lambda: False)
    monkeypatch.setattr(suite_module, "run_retrieval_evaluation", lambda vector_store, neo4j_client: [])
    monkeypatch.setattr(suite_module, "run_rag_evaluation", lambda vector_store, llm_judge=None: [])
    monkeypatch.setattr(suite_module, "VectorStore", lambda url: object())

    report = suite_module.run_full_suite(database_url="postgresql://irrelevant")

    skipped_categories = {r.category for r in report.results if r.skipped}
    assert skipped_categories == {"agent", "modification", "testing_loop"}
    assert all("Neo4j not reachable" in r.skip_reason for r in report.results if r.skipped)
