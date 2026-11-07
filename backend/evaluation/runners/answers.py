"""F7 answer-quality evaluation of `HybridRAGService.answer` — the exact
service the Ask endpoint calls — split strictly in two:

DETERMINISTIC (category "answers"; stub LLM, never reads answer text):
- citation correctness: every cited (path, start_line, end_line) is an
  indexed chunk AND those lines on disk equal that chunk's content, so a
  citation always points at the exact code the model was shown;
- source attribution: for answerable questions, an expected file is cited
  with LEXICAL evidence (`found_via` includes "keyword"); for questions the
  repository cannot answer, no source may claim keyword evidence;
- `ask_flags_insufficient_evidence` (informational): whether the
  service's own signal — zero context chunks, which is what Ask's
  `no_relevant_context` warning reports — fires. With hash embeddings,
  vector search always returns its top-k, so this is EXPECTED to be 0:
  the system does not detect insufficient evidence by itself; only a
  real model reading the context can. Reported, not hidden.

REAL MODEL (category "answers_real_model"; evaluation_kind="real_model"):
the generated text must mention an expected identifier (answerable) or
explicitly acknowledge missing information (insufficient). Run ONLY when
a real LLM was requested and LLM_API_KEY is configured; otherwise every
case is reported as skipped — no score is ever produced from the stub.
"""

import re
import tempfile
import time
from pathlib import Path
from typing import Any, List, Optional

from graph.builder import GraphBuilder
from graph.client import Neo4jClient
from hybrid.models import HybridAnswer
from hybrid.service import HybridRAGService
from ingestion.service import IngestionService
from parsing.service import ParsingService
from rag.llm.base import LLMProvider
from rag.llm.stub_provider import StubLLMProvider
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore

from evaluation.cleanup import delete_evaluation_graph, delete_evaluation_repository
from evaluation.dataset import ANSWER_CASES, AnswerCase, build_sample_repository
from evaluation.models import KIND_REAL_MODEL, CaseResult, MetricResult

CATEGORY = "answers"
REAL_MODEL_CATEGORY = "answers_real_model"

# Phrases a grounded model uses to say the context does not answer the
# question. A heuristic over real model output, which is why it only gates
# the real-model category.
_INSUFFICIENT_PATTERN = re.compile(
    r"(does not|doesn't|do not|don't) (contain|include|show|mention|appear|have|provide)"
    r"|no (information|code|mention|evidence|relevant)|not (enough|sufficient|present|found)"
    r"|insufficient|cannot (find|determine|answer|tell)|can't (find|determine|answer|tell)|unable to",
    re.IGNORECASE,
)


class _NoGraphClient:
    def run(self, query: str, **params: Any) -> list:
        return []

    def close(self) -> None:
        pass


def _flag(name: str, ok: bool) -> MetricResult:
    return MetricResult(name=name, value=1.0 if ok else 0.0, expected=1.0, passed=ok)


def _indexed_chunks(vector_store: VectorStore, repository_id: int) -> dict:
    conn = vector_store.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT relative_path, start_line, end_line, content FROM code_chunks WHERE repository_id = %s",
                (repository_id,),
            )
            chunks: dict = {}
            for path, start, end, content in cur.fetchall():
                chunks.setdefault((path, start, end), []).append(content)
            return chunks
    finally:
        conn.close()


def citation_matches(root: Path, source: Any, indexed: dict) -> bool:
    """The cited lines exist, are an indexed chunk, and equal it on disk."""
    contents = indexed.get((source.relative_path, source.start_line, source.end_line))
    path = root / source.relative_path
    if not contents or not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    if source.start_line < 1 or source.end_line > len(lines) or source.end_line < source.start_line:
        return False
    on_disk = "\n".join(lines[source.start_line - 1 : source.end_line])
    return any(c.rstrip("\n") == on_disk.rstrip("\n") for c in contents)


def _deterministic_case(case: AnswerCase, answer: HybridAnswer, root: Path, indexed: dict, latency: float) -> CaseResult:
    sources = answer.sources
    cited = [s.relative_path for s in sources]
    lexical = [s for s in sources if "keyword" in s.found_via]
    metrics = [
        _flag("citations_match_indexed_code_on_disk", all(citation_matches(root, s, indexed) for s in sources)),
        _flag("citations_unique", len({(s.relative_path, s.start_line, s.end_line) for s in sources}) == len(sources)),
    ]
    if case.answerable:
        metrics.append(_flag("expected_file_cited", bool(set(cited) & case.expected_files)))
        metrics.append(
            _flag("expected_file_has_lexical_evidence", any(s.relative_path in case.expected_files for s in lexical))
        )
        # Informational: how much of what was cited is actually relevant.
        precision = sum(1 for p in cited if p in case.expected_files) / len(cited) if cited else 0.0
        metrics.append(MetricResult(name="citation_precision (informational)", value=precision))
    else:
        metrics.append(_flag("no_lexical_evidence_claimed", not lexical))
        metrics.append(
            MetricResult(
                name="ask_flags_insufficient_evidence (informational; 0 expected with hash embeddings)",
                value=1.0 if answer.context_chunk_count == 0 else 0.0,
            )
        )
    metrics.append(MetricResult(name="context_chunk_count", value=float(answer.context_chunk_count)))
    gating = [m for m in metrics if m.passed is not None]
    failed = [m.name for m in gating if not m.passed]
    return CaseResult(
        case_id=case.case_id,
        category=CATEGORY,
        description=case.description,
        passed=not failed,
        metrics=metrics,
        latency_seconds=latency,
        retrieval_results=[f"{s.relative_path}:{s.start_line}-{s.end_line} via {','.join(s.found_via)}" for s in sources],
        failure_reason=f"failed: {', '.join(failed)}" if failed else None,
    )


def acknowledges_insufficient_evidence(text: str) -> bool:
    return bool(_INSUFFICIENT_PATTERN.search(text or ""))


def _real_model_case(case: AnswerCase, answer: HybridAnswer, latency: float) -> CaseResult:
    text = answer.answer or ""
    if case.answerable:
        ok = any(term.lower() in text.lower() for term in case.expected_terms)
        metric = _flag("answer_mentions_expected_identifier", ok)
    else:
        ok = acknowledges_insufficient_evidence(text)
        metric = _flag("answer_acknowledges_insufficient_evidence", ok)
    return CaseResult(
        case_id=f"{case.case_id}-real-model",
        category=REAL_MODEL_CATEGORY,
        description=case.description,
        passed=ok,
        metrics=[metric],
        latency_seconds=latency,
        evaluation_kind=KIND_REAL_MODEL,
        failure_reason=None if ok else f"{metric.name} not satisfied",
    )


def skipped_real_model_cases(reason: str) -> List[CaseResult]:
    return [
        CaseResult(
            case_id=f"{case.case_id}-real-model",
            category=REAL_MODEL_CATEGORY,
            description=case.description,
            passed=False,
            skipped=True,
            skip_reason=reason,
            evaluation_kind=KIND_REAL_MODEL,
        )
        for case in ANSWER_CASES
    ]


def _cleanup(vector_store: VectorStore, repository_id: int, neo4j_client: Optional[Neo4jClient], root_path: str) -> None:
    delete_evaluation_repository(vector_store, repository_id)
    if neo4j_client is not None:
        delete_evaluation_graph(neo4j_client, root_path)


def run_answer_evaluation(
    vector_store: VectorStore,
    neo4j_client: Optional[Neo4jClient] = None,
    embedding_provider: Optional[EmbeddingProvider] = None,
    real_llm_provider: Optional[LLMProvider] = None,
    real_llm_skip_reason: str = "Real LLM not requested (run with --real-llm and LLM_API_KEY)",
    cases: Optional[List[AnswerCase]] = None,
) -> List[CaseResult]:
    """Deterministic cases always run with `StubLLMProvider`. Real-model
    cases run only when `real_llm_provider` is given; the SAME retrieval
    results are used for both, so they differ only in the generator."""
    embedding_provider = embedding_provider or DeterministicLocalEmbeddingProvider()
    cases = cases if cases is not None else ANSWER_CASES
    results: List[CaseResult] = []
    real_results: List[CaseResult] = []

    with tempfile.TemporaryDirectory(prefix="eval-answers-") as tmp_dir:
        root = Path(tmp_dir).resolve()
        build_sample_repository(root)
        ingestion_result = IngestionService().ingest(str(root))
        parsing_result = ParsingService().parse_repository(str(root), ingestion_result)
        index_result = IndexingService(embedding_provider, vector_store).index_repository(str(root))
        graph: Any = neo4j_client if neo4j_client is not None else _NoGraphClient()
        if neo4j_client is not None:
            GraphBuilder(neo4j_client).build(str(root), ingestion_result, parsing_result)
        try:
            indexed = _indexed_chunks(vector_store, index_result.repository_id)
            for case in cases:
                start = time.monotonic()
                stub_answer = HybridRAGService(vector_store, graph, embedding_provider, StubLLMProvider()).answer(
                    case.question, index_result.repository_id, str(root)
                )
                results.append(_deterministic_case(case, stub_answer, root, indexed, time.monotonic() - start))
                if real_llm_provider is not None:
                    start = time.monotonic()
                    real_answer = HybridRAGService(vector_store, graph, embedding_provider, real_llm_provider).answer(
                        case.question, index_result.repository_id, str(root)
                    )
                    real_results.append(_real_model_case(case, real_answer, time.monotonic() - start))
        finally:
            _cleanup(vector_store, index_result.repository_id, neo4j_client, str(root))

    return results + (real_results if real_llm_provider is not None else skipped_real_model_cases(real_llm_skip_reason))
