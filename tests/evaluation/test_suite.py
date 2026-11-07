"""Unit tests for run_full_suite's skip behavior, with
postgres_available/neo4j_available monkeypatched — proving the suite
degrades to explicit skipped CaseResults (never a crash, never a
silently-dropped category) when a service isn't reachable. Real,
end-to-end suite behavior against live services is exercised by
`backend/scripts/run_evaluation.py` and each category's own runner
tests.
"""

import evaluation.suite as suite_module


def _no_side_categories(monkeypatch):
    """The two categories that don't use DATABASE_URL, stubbed out."""
    monkeypatch.setattr(suite_module, "run_agent_lifecycle_evaluation", lambda url, **kwargs: [])
    monkeypatch.setattr(suite_module, "describe_environment", lambda *a, **k: {"database": "stub"})


def test_side_categories_protect_the_suites_database(monkeypatch):
    """The lifecycle and regression runners are told which database they
    must never use: the one the rest of the suite evaluates against."""
    seen = {}
    monkeypatch.setattr(suite_module, "postgres_available", lambda url: False)
    monkeypatch.setattr(suite_module, "describe_environment", lambda *a, **k: {})
    monkeypatch.setattr(
        suite_module, "run_agent_lifecycle_evaluation", lambda url, **kw: seen.setdefault("lifecycle", kw["protected_database_url"]) and []
    )
    monkeypatch.setattr(
        suite_module,
        "run_regression_evaluation",
        lambda **kw: seen.setdefault("regression", (kw["protected_database_url"], kw["isolated_database_url"])) and [],
    )

    suite_module.run_full_suite("postgresql://app-db/x", isolated_database_url="postgresql://eval-db/y", with_regression=True)

    # Regression also writes to the isolated database when one is given.
    assert seen == {"lifecycle": "postgresql://app-db/x", "regression": ("postgresql://app-db/x", "postgresql://eval-db/y")}


def test_all_categories_skipped_when_postgres_unreachable(monkeypatch):
    monkeypatch.setattr(suite_module, "postgres_available", lambda url: False)
    _no_side_categories(monkeypatch)

    report = suite_module.run_full_suite(database_url="postgresql://irrelevant")

    assert all(r.skipped for r in report.results)
    database_categories = {"retrieval", "rag", "answers", "answers_real_model", "agent", "modification", "modification_safety", "testing_loop"}
    assert database_categories <= {r.category for r in report.results}
    assert all("PostgreSQL not reachable" in (r.skip_reason or "") for r in report.results if r.category in database_categories)
    # F7: regression is opt-in and says so rather than vanishing.
    assert all("--with-regression" in r.skip_reason for r in report.results if r.category == "regression")


def test_agent_modification_testing_loop_skipped_when_neo4j_unreachable(monkeypatch):
    monkeypatch.setattr(suite_module, "postgres_available", lambda url: True)
    monkeypatch.setattr(suite_module, "neo4j_available", lambda: False)
    monkeypatch.setattr(suite_module, "run_retrieval_evaluation", lambda vector_store, neo4j_client, *a: [])
    monkeypatch.setattr(suite_module, "run_rag_evaluation", lambda vector_store, llm_judge=None: [])
    monkeypatch.setattr(suite_module, "run_answer_evaluation", lambda *a, **k: [])
    monkeypatch.setattr(suite_module, "run_modification_safety_evaluation", lambda vector_store: [])
    monkeypatch.setattr(suite_module, "VectorStore", lambda url: object())
    monkeypatch.setattr(suite_module, "find_leftover_repositories", lambda vector_store: [])
    _no_side_categories(monkeypatch)

    report = suite_module.run_full_suite(database_url="postgresql://irrelevant")

    skipped_categories = {r.category for r in report.results if r.skipped and r.category != "regression"}
    assert skipped_categories == {"agent", "modification", "testing_loop"}
    assert all("Neo4j not reachable" in r.skip_reason for r in report.results if r.skipped and r.category != "regression")


def test_real_llm_requested_without_a_key_is_skipped_never_scored(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    provider, reason = suite_module._real_llm(True)
    assert provider is None and "LLM_API_KEY is not set" in reason
    provider, reason = suite_module._real_llm(False)
    assert provider is None and "--real-llm" in reason


def test_real_embeddings_need_a_key(monkeypatch):
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    assert suite_module._real_embeddings(True) is None
