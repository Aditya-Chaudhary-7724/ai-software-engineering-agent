"""Integration tests for the RAG evaluation runner — real PostgreSQL,
real retrieval/ranking/context-assembly pipeline, StubLLMProvider (no
LLM_API_KEY needed for these deterministic checks).
"""

from evaluation.dataset import RAG_CASES
from evaluation.runners.rag import run_rag_evaluation

from tests.evaluation.conftest import requires_postgres, vector_store


@requires_postgres
def test_returns_one_result_per_case(vector_store):
    results = run_rag_evaluation(vector_store)

    assert {r.case_id for r in results} == {c.case_id for c in RAG_CASES}
    assert all(r.category == "rag" for r in results)


@requires_postgres
def test_all_deterministic_cases_pass_against_the_fixed_dataset(vector_store):
    """These cases were hand-designed against the fixed sample
    repository (see dataset.py) — a failure here means either the
    dataset's expectations or the retrieval/context pipeline itself
    regressed, not that a real LLM produced a bad answer.
    """
    results = run_rag_evaluation(vector_store)

    for result in results:
        assert result.passed, f"{result.case_id} failed: {[m for m in result.metrics if m.passed is False]}"


@requires_postgres
def test_no_llm_judge_metric_present_by_default(vector_store):
    results = run_rag_evaluation(vector_store)

    for result in results:
        assert not any("llm_judge" in m.name for m in result.metrics)


@requires_postgres
def test_llm_judge_metric_is_informational_when_provided(vector_store):
    class _FixedJudge:
        def score_answer(self, question, context, answer):
            return 0.75

    results = run_rag_evaluation(vector_store, llm_judge=_FixedJudge())

    for result in results:
        judge_metric = next(m for m in result.metrics if "llm_judge" in m.name)
        assert judge_metric.value == 0.75
        assert judge_metric.passed is None  # never gates pass/fail
