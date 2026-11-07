"""Ties every evaluation runner together into one `run_full_suite()`
call, the same "one entry point wiring existing pieces together"
pattern as `AgentService`/`IndexingService` elsewhere in this project.

Each category is skipped (as explicit `CaseResult(skipped=True)`
entries, never silently dropped) rather than failed when the external
service it needs isn't reachable — the same "LOCAL TESTED vs REQUIRES
EXTERNAL SERVICE" distinction as this project's pytest suite, applied
here so `run_evaluation.py` behaves the same way on a machine missing
Neo4j or Docker.

F7 adds four categories, all deterministic unless stated:
- `answers` (+ `answers_real_model`, real LLM only, opt-in): the service
  behind Ask — citation correctness, attribution, insufficient evidence;
- `modification_safety`: protected targets, diff integrity, deleted/binary
  targets, no-op diffs, cross-repository confinement, no-rollback honesty;
- `agent_lifecycle`: the F6 task/approval lifecycle — runs ONLY against an
  explicitly isolated database (see runners/agent_lifecycle.py);
- `regression` (opt-in): re-runs the existing security/approval pytest
  suites and reports their exact counts.
Real providers are used only when requested AND configured; their results
are marked `evaluation_kind="real_model"` and reported separately.

WHERE TEMPORARY ROWS GO: with `isolated_database_url` (CLI:
--isolated-database-url / $EVALUATION_DATABASE_URL), EVERY category writes
there — never to DATABASE_URL. If it is unreachable, or the server reports
it is the same database as DATABASE_URL, the database categories are
reported as skipped with that reason; there is no fallback. Without it,
the Phase 12 behaviour remains: temporary rows in DATABASE_URL, deleted per
case by namespace (`evaluation/cleanup.py`). Neo4j has no second instance
here, so graph data is namespaced by the temporary repository path and
removed the same guarded way — namespaced, not isolated.
"""

import dataclasses

import os
from typing import List, Optional, Tuple

import psycopg

from graph.client import Neo4jClient
from vectorstore.migrations import apply_schema
from vectorstore.store import VectorStore

from evaluation.cleanup import find_leftover_graph_roots, find_leftover_repositories

from evaluation.environment import describe_environment, neo4j_available, postgres_available
from evaluation.llm_judge import LLMJudge
from evaluation.models import KIND_REAL_MODEL, CaseResult, EvaluationReport
from evaluation.runners.agent import run_agent_evaluation
from evaluation.runners.agent_lifecycle import run_agent_lifecycle_evaluation
from evaluation.runners.answers import run_answer_evaluation, skipped_real_model_cases
from evaluation.runners.modification_safety import run_modification_safety_evaluation
from evaluation.runners.regression import run_regression_evaluation, skipped_regression
from evaluation.runners.modification import run_modification_evaluation
from evaluation.runners.rag import run_rag_evaluation
from evaluation.runners.retrieval import run_retrieval_evaluation
from evaluation.runners.testing_loop import run_testing_loop_evaluation
from evaluation.safety import DEFAULT_DATABASE_URL, UnsafeEvaluationTargetError, application_database_url, require_distinct_database

__all__ = ["DEFAULT_DATABASE_URL", "run_full_suite"]

_DATABASE_CATEGORIES = (
    ("retrieval", "retrieval-suite", "Retrieval evaluation"),
    ("rag", "rag-suite", "RAG evaluation"),
    ("answers", "answers-suite", "Answer-quality evaluation"),
    ("agent", "agent-suite", "Agent evaluation"),
    ("modification", "modification-suite", "Modification evaluation"),
    ("modification_safety", "modification-safety-suite", "Modification-safety evaluation"),
    ("testing_loop", "testing-loop-suite", "Testing-loop evaluation"),
)


def _skip(category: str, case_id: str, description: str, reason: str) -> CaseResult:
    return CaseResult(case_id=case_id, category=category, description=description, passed=False, skipped=True, skip_reason=reason)


def _skip_database_categories(reason: str) -> List[CaseResult]:
    results = [_skip(category, case_id, description, reason) for category, case_id, description in _DATABASE_CATEGORIES]
    return results + skipped_real_model_cases(reason)


def select_evaluation_database(database_url: str, isolated_database_url: Optional[str]) -> Tuple[Optional[str], str]:
    """(database the categories write to, description). None means: do not
    run them; the description is the reason. Never falls back to
    `database_url` once an isolated database was asked for."""
    if not isolated_database_url:
        return database_url, "DATABASE_URL (no isolated database given; temporary rows removed per case)"
    try:
        require_distinct_database(isolated_database_url, database_url, purpose="isolated evaluation database")
        with psycopg.connect(isolated_database_url, connect_timeout=5) as conn:
            apply_schema(conn)
    except UnsafeEvaluationTargetError as exc:
        return None, f"REFUSED: {exc}"
    except psycopg.OperationalError:
        return None, "isolated database not reachable; refusing to fall back to DATABASE_URL (REQUIRES EXTERNAL SERVICE)"
    return isolated_database_url, "isolated database"


def _real_llm(requested: bool):
    if not requested:
        return None, "Real LLM not requested (run with --real-llm; requires LLM_API_KEY)"
    if not os.environ.get("LLM_API_KEY"):
        return None, "--real-llm given but LLM_API_KEY is not set (REQUIRES REAL PROVIDER)"
    from rag.llm.anthropic_provider import AnthropicLLMProvider

    return AnthropicLLMProvider(), ""


def _real_embeddings(requested: bool):
    if not requested or not os.environ.get("EMBEDDING_API_KEY"):
        return None
    from vectorstore.embeddings.openai_provider import OpenAIEmbeddingProvider

    return OpenAIEmbeddingProvider()


def _as_real_model(results: List[CaseResult]) -> List[CaseResult]:
    return [dataclasses.replace(r, evaluation_kind=KIND_REAL_MODEL) for r in results]


def run_full_suite(
    database_url: Optional[str] = None,
    llm_judge: Optional[LLMJudge] = None,
    *,
    isolated_database_url: Optional[str] = None,
    real_llm: bool = False,
    real_embeddings: bool = False,
    with_regression: bool = False,
) -> EvaluationReport:
    database_url = database_url or application_database_url()
    environment = describe_environment(
        database_url,
        real_llm_requested=real_llm,
        real_embeddings_requested=real_embeddings,
        isolated_database_url=isolated_database_url or "",
    )
    results: List[CaseResult] = []

    # Never against `database_url` itself: the lifecycle runner uses the
    # isolated database, and so do the regression suites when one is given
    # (else pytest's TEST_DATABASE_URL); both refuse a database the server
    # reports is `database_url`, and regression refuses an unreachable one.
    results.extend(run_agent_lifecycle_evaluation(isolated_database_url, protected_database_url=database_url))
    results.extend(run_regression_evaluation(protected_database_url=database_url, isolated_database_url=isolated_database_url)
                   if with_regression else skipped_regression(
        "not requested (run with --with-regression; re-runs the existing security/approval pytest suites)"
    ))

    evaluation_database_url, writes_to = select_evaluation_database(database_url, isolated_database_url)
    environment["evaluation_writes"] = writes_to
    if evaluation_database_url is None:
        results.extend(_skip_database_categories(writes_to))
        return EvaluationReport(results=results, environment=environment)
    if not postgres_available(evaluation_database_url):
        results.extend(_skip_database_categories("PostgreSQL not reachable (REQUIRES EXTERNAL SERVICE)"))
        return EvaluationReport(results=results, environment=environment)

    vector_store = VectorStore(evaluation_database_url)
    neo4j_ready = neo4j_available()
    llm_provider, llm_skip_reason = _real_llm(real_llm)
    embedding_provider = _real_embeddings(real_embeddings)

    neo4j_client: Optional[Neo4jClient] = Neo4jClient() if neo4j_ready else None
    try:
        if embedding_provider is None:
            results.extend(run_retrieval_evaluation(vector_store, neo4j_client))
        else:
            results.extend(_as_real_model(run_retrieval_evaluation(vector_store, neo4j_client, embedding_provider)))
        results.extend(run_rag_evaluation(vector_store, llm_judge=llm_judge))
        answers = run_answer_evaluation(
            vector_store,
            neo4j_client,
            embedding_provider=embedding_provider,
            real_llm_provider=llm_provider,
            real_llm_skip_reason=llm_skip_reason,
        )
        results.extend(answers if embedding_provider is None else _as_real_model(answers))
        results.extend(run_modification_safety_evaluation(vector_store))

        if neo4j_ready:
            assert neo4j_client is not None
            results.extend(run_agent_evaluation(vector_store, neo4j_client))
            results.extend(run_modification_evaluation(vector_store, neo4j_client))
            results.extend(run_testing_loop_evaluation(vector_store, neo4j_client))
        else:
            reason = "Neo4j not reachable (REQUIRES EXTERNAL SERVICE)"
            results.append(_skip("agent", "agent-suite", "Agent evaluation", reason))
            results.append(_skip("modification", "modification-suite", "Modification evaluation", reason))
            results.append(_skip("testing_loop", "testing-loop-suite", "Testing-loop evaluation", reason))
        # Rows/nodes a KILLED earlier run left behind (a normal run, even one
        # whose cases raise, leaves none). Reported, never silently removed.
        environment["leftover_evaluation_repositories"] = str(len(find_leftover_repositories(vector_store)))
        if neo4j_client is not None:
            environment["leftover_evaluation_graphs"] = str(len(find_leftover_graph_roots(neo4j_client)))
    finally:
        if neo4j_client is not None:
            neo4j_client.close()

    return EvaluationReport(results=results, environment=environment)
