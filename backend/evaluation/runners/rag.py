"""RAG evaluation: does the assembled context actually contain the
expected evidence, and are citations (sources) grounded in what was
actually retrieved — all fully deterministic, computed from the
retrieval/ranking/context-assembly pipeline BEFORE the LLM is ever
called (`rag.ranking.merge_and_rank`, `rag.context.build_context`, the
real Phase 3/4 code, not reimplemented here). None of this depends on
`StubLLMProvider`'s placeholder text meaning anything — see that
provider's own docstring, and do not read its "answer" text as a
quality signal.

`answer_is_non_empty` is the one thing checked about the generated
text itself, and deliberately not more than that: whether a free-text
answer is *complete* or *fluent* is not a deterministic property this
package claims to measure. An optional LLM-judge score is available
(see `evaluation/llm_judge.py`), always informational, never a pass/
fail gate, and only computed when a real `LLMJudge` is passed in.
"""

import tempfile
import time
from pathlib import Path
from typing import List, Optional

from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore

from rag.context import build_context
from rag.keyword_search import keyword_search
from rag.llm.stub_provider import StubLLMProvider
from rag.prompt import SYSTEM_PROMPT, build_prompt
from rag.ranking import merge_and_rank

from evaluation.dataset import RAG_CASES, RAGCase, build_sample_repository
from evaluation.llm_judge import LLMJudge
from evaluation.metrics import recall_at_k
from evaluation.models import CaseResult, MetricResult

_TOP_K = 10


def _cleanup_repository(vector_store: VectorStore, repository_id: int) -> None:
    conn = vector_store.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM code_chunks WHERE repository_id = %s", (repository_id,))
            cur.execute("DELETE FROM repositories WHERE id = %s", (repository_id,))
        conn.commit()
    finally:
        conn.close()


def _evaluate_case(
    case: RAGCase,
    vector_store: VectorStore,
    embedding_provider: EmbeddingProvider,
    repository_id: int,
    llm_judge: Optional[LLMJudge],
) -> CaseResult:
    start = time.monotonic()
    conn = vector_store.connect()
    try:
        query_embedding = embedding_provider.embed([case.query])[0]
        vector_hits = vector_store.similarity_search(conn, query_embedding, top_k=_TOP_K, repository_id=repository_id)
        keyword_hits = keyword_search(conn, case.query, top_k=_TOP_K, repository_id=repository_id)
    finally:
        conn.close()

    candidates = merge_and_rank(vector_hits, keyword_hits, top_k=5)
    context_text, included = build_context(candidates)

    evidence_files = [c.relative_path for c in included]
    evidence_recall = recall_at_k(evidence_files, case.expected_evidence_files, k=max(len(evidence_files), 1))

    found_substrings = [s for s in case.expected_context_substrings if s in context_text]
    substring_coverage = len(found_substrings) / len(case.expected_context_substrings)

    # The generated "answer" itself, with the explicitly non-LLM stub —
    # proves the pipeline wires context -> prompt -> provider -> sources
    # correctly, never used as a stand-in for real answer quality.
    answer_text = StubLLMProvider().generate(build_prompt(case.query, context_text), system=SYSTEM_PROMPT)
    sources = [c.relative_path for c in included]
    citations_grounded = all(path in evidence_files for path in sources)

    metrics = [
        MetricResult(name="evidence_recall", value=evidence_recall, expected=1.0, passed=evidence_recall >= 1.0),
        MetricResult(
            name="context_substring_coverage", value=substring_coverage, expected=1.0, passed=substring_coverage >= 1.0
        ),
        MetricResult(name="answer_is_non_empty", value=1.0 if answer_text else 0.0, expected=1.0, passed=bool(answer_text)),
        MetricResult(
            name="citations_grounded_in_retrieved_context",
            value=1.0 if citations_grounded else 0.0,
            expected=1.0,
            passed=citations_grounded,
        ),
    ]

    if llm_judge is not None:
        # Informational only — see this module's and llm_judge.py's docstrings.
        judge_score = llm_judge.score_answer(case.query, context_text, answer_text)
        metrics.append(MetricResult(name="llm_judge_score (REQUIRES REAL LLM, informational)", value=judge_score))

    gating_metrics = [m for m in metrics if m.passed is not None]
    passed = all(m.passed for m in gating_metrics)

    return CaseResult(
        case_id=case.case_id,
        category="rag",
        description=case.description,
        passed=passed,
        metrics=metrics,
        latency_seconds=time.monotonic() - start,
        retrieval_results=evidence_files,
    )


def run_rag_evaluation(
    vector_store: VectorStore,
    embedding_provider: Optional[EmbeddingProvider] = None,
    llm_judge: Optional[LLMJudge] = None,
) -> List[CaseResult]:
    embedding_provider = embedding_provider or DeterministicLocalEmbeddingProvider()

    with tempfile.TemporaryDirectory(prefix="eval-rag-") as tmp_dir:
        root = Path(tmp_dir)
        build_sample_repository(root)

        index_result = IndexingService(embedding_provider, vector_store).index_repository(str(root))

        try:
            return [
                _evaluate_case(case, vector_store, embedding_provider, index_result.repository_id, llm_judge)
                for case in RAG_CASES
            ]
        finally:
            _cleanup_repository(vector_store, index_result.repository_id)
