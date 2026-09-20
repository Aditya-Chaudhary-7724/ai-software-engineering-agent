"""Ties every evaluation runner together into one `run_full_suite()`
call, the same "one entry point wiring existing pieces together"
pattern as `AgentService`/`IndexingService` elsewhere in this project.

Each category is skipped (as explicit `CaseResult(skipped=True)`
entries, never silently dropped) rather than failed when the external
service it needs isn't reachable — the same "LOCAL TESTED vs REQUIRES
EXTERNAL SERVICE" distinction as this project's pytest suite, applied
here so `run_evaluation.py` behaves the same way on a machine missing
Neo4j or Docker.
"""

import getpass
import os
from typing import List, Optional

from graph.client import Neo4jClient
from vectorstore.store import VectorStore

from evaluation.environment import neo4j_available, postgres_available
from evaluation.llm_judge import LLMJudge
from evaluation.models import CaseResult, EvaluationReport
from evaluation.runners.agent import run_agent_evaluation
from evaluation.runners.modification import run_modification_evaluation
from evaluation.runners.rag import run_rag_evaluation
from evaluation.runners.retrieval import run_retrieval_evaluation
from evaluation.runners.testing_loop import run_testing_loop_evaluation

DEFAULT_DATABASE_URL = f"postgresql://{getpass.getuser()}@localhost:5432/ai_swe_agent"


def _skip(category: str, case_id: str, description: str, reason: str) -> CaseResult:
    return CaseResult(case_id=case_id, category=category, description=description, passed=False, skipped=True, skip_reason=reason)


def run_full_suite(database_url: Optional[str] = None, llm_judge: Optional[LLMJudge] = None) -> EvaluationReport:
    database_url = database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    results: List[CaseResult] = []

    if not postgres_available(database_url):
        results.append(
            _skip("retrieval", "retrieval-suite", "Retrieval evaluation", "PostgreSQL not reachable (REQUIRES EXTERNAL SERVICE)")
        )
        results.append(_skip("rag", "rag-suite", "RAG evaluation", "PostgreSQL not reachable (REQUIRES EXTERNAL SERVICE)"))
        results.append(_skip("agent", "agent-suite", "Agent evaluation", "PostgreSQL not reachable (REQUIRES EXTERNAL SERVICE)"))
        results.append(
            _skip("modification", "modification-suite", "Modification evaluation", "PostgreSQL not reachable (REQUIRES EXTERNAL SERVICE)")
        )
        results.append(
            _skip("testing_loop", "testing-loop-suite", "Testing-loop evaluation", "PostgreSQL not reachable (REQUIRES EXTERNAL SERVICE)")
        )
        return EvaluationReport(results=results)

    vector_store = VectorStore(database_url)
    neo4j_ready = neo4j_available()

    neo4j_client: Optional[Neo4jClient] = Neo4jClient() if neo4j_ready else None
    try:
        results.extend(run_retrieval_evaluation(vector_store, neo4j_client))
        results.extend(run_rag_evaluation(vector_store, llm_judge=llm_judge))

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
    finally:
        if neo4j_client is not None:
            neo4j_client.close()

    return EvaluationReport(results=results)
