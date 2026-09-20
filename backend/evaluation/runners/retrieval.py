"""Retrieval evaluation: vector, keyword, RAG-hybrid (Phase 4: vector +
keyword), and graph-augmented hybrid (Phase 6: vector + keyword +
graph) retrieval — scored with real Recall@K, Precision@K, reciprocal
rank, and hit rate against the fixed cases in `evaluation/dataset.py`,
run through the REAL Phase 3/4/5/6 retrieval code (`VectorStore`,
`keyword_search`, `rag.ranking`, `hybrid.ranking`,
`hybrid.graph_expansion`) — none of it reimplemented here.

IMPORTANT, load-bearing for interpreting these numbers honestly:
vector-retrieval metrics reflect `DeterministicLocalEmbeddingProvider`
by default — a hash-based, explicitly NON-SEMANTIC stand-in (see its
own docstring), used here for the same reason it's used throughout this
project's test suite: no `EMBEDDING_API_KEY` is assumed present.
Semantically similar text does NOT produce similar vectors with it, so
vector-only recall/precision measures nothing about real embedding
quality — a low number there is expected, not a bug. Pass `--real-embeddings`
to `run_evaluation.py` (which passes `OpenAIEmbeddingProvider` here
instead) to get a meaningful vector-retrieval number, which requires a
real `EMBEDDING_API_KEY`. Keyword search (PostgreSQL full-text, no
embeddings involved) and the hybrid methods that include it are the
metrics that are informative in this environment; `passed` therefore
gates on keyword/hybrid hit rate, and vector-only metrics are reported
as informational (`passed=None`) rather than silently hidden or
misleadingly marked "failed" for a documented, expected limitation.
"""

import tempfile
import time
from pathlib import Path
from typing import List, Optional

from graph.builder import GraphBuilder
from graph.client import Neo4jClient
from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService
from vectorstore.store import VectorStore

from rag.keyword_search import keyword_search
from rag.ranking import merge_and_rank as merge_rag_hybrid

from hybrid.graph_expansion import build_graph_candidates
from hybrid.ranking import merge_and_rank as merge_full_hybrid

from evaluation.dataset import RETRIEVAL_CASES, RetrievalCase, build_sample_repository
from evaluation.metrics import hit_rate, precision_at_k, recall_at_k, reciprocal_rank
from evaluation.models import CaseResult, MetricResult

_TOP_K_FOR_MERGE = 10


def _cleanup_repository(vector_store: VectorStore, repository_id: int) -> None:
    """Scoped cleanup — deletes only the rows this run created, never a
    blanket TRUNCATE, since a caller may be running this evaluation
    against a real development database with other data in it.
    """
    conn = vector_store.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM code_chunks WHERE repository_id = %s", (repository_id,))
            cur.execute("DELETE FROM repositories WHERE id = %s", (repository_id,))
        conn.commit()
    finally:
        conn.close()


def _cleanup_graph(neo4j_client: Neo4jClient, root_path: str) -> None:
    neo4j_client.run("MATCH (n {repository_root_path: $root_path}) DETACH DELETE n", root_path=root_path)
    neo4j_client.run("MATCH (r:Repository {root_path: $root_path}) DETACH DELETE r", root_path=root_path)


def _metric(name: str, retrieved: List[str], relevant, k: int, gate: bool) -> List[MetricResult]:
    recall = recall_at_k(retrieved, relevant, k)
    precision = precision_at_k(retrieved, relevant, k)
    rr = reciprocal_rank(retrieved, relevant)
    hr = hit_rate(retrieved, relevant)
    passed = (hr >= 1.0) if gate else None
    return [
        MetricResult(name=f"{name}_recall@{k}", value=recall),
        MetricResult(name=f"{name}_precision@{k}", value=precision),
        MetricResult(name=f"{name}_reciprocal_rank", value=rr),
        MetricResult(name=f"{name}_hit_rate", value=hr, expected=1.0, passed=passed),
    ]


def _evaluate_case(
    case: RetrievalCase,
    vector_store: VectorStore,
    embedding_provider: EmbeddingProvider,
    neo4j_client: Optional[Neo4jClient],
    repository_id: int,
    root_path: str,
) -> CaseResult:
    start = time.monotonic()
    conn = vector_store.connect()
    try:
        query_embedding = embedding_provider.embed([case.query])[0]
        vector_hits = vector_store.similarity_search(
            conn, query_embedding, top_k=_TOP_K_FOR_MERGE, repository_id=repository_id
        )
        keyword_hits = keyword_search(conn, case.query, top_k=_TOP_K_FOR_MERGE, repository_id=repository_id)
        rag_hybrid = merge_rag_hybrid(vector_hits, keyword_hits, top_k=_TOP_K_FOR_MERGE)

        graph_candidates = None
        if neo4j_client is not None:
            seeds = merge_rag_hybrid(vector_hits, keyword_hits, top_k=5)
            graph_hits = build_graph_candidates(neo4j_client, conn, repository_id, root_path, seeds)
            graph_candidates = merge_full_hybrid(vector_hits, keyword_hits, graph_hits, top_k=_TOP_K_FOR_MERGE)
    finally:
        conn.close()

    vector_paths = [h.relative_path for h in vector_hits]
    keyword_paths = [h.relative_path for h in keyword_hits]
    rag_hybrid_paths = [c.relative_path for c in rag_hybrid]

    metrics: List[MetricResult] = []
    metrics += _metric("vector", vector_paths, case.expected_relevant_files, case.k, gate=False)
    metrics += _metric("keyword", keyword_paths, case.expected_relevant_files, case.k, gate=True)
    metrics += _metric("rag_hybrid", rag_hybrid_paths, case.expected_relevant_files, case.k, gate=True)

    all_retrieved = set(vector_paths) | set(keyword_paths)
    if graph_candidates is not None:
        graph_hybrid_paths = [c.relative_path for c in graph_candidates]
        metrics += _metric("graph_hybrid", graph_hybrid_paths, case.expected_relevant_files, case.k, gate=True)
        all_retrieved |= set(graph_hybrid_paths)

    gating_metrics = [m for m in metrics if m.passed is not None]
    passed = all(m.passed for m in gating_metrics) if gating_metrics else False

    return CaseResult(
        case_id=case.case_id,
        category="retrieval",
        description=case.description,
        passed=passed,
        metrics=metrics,
        latency_seconds=time.monotonic() - start,
        retrieval_results=sorted(all_retrieved),
    )


def run_retrieval_evaluation(
    vector_store: VectorStore,
    neo4j_client: Optional[Neo4jClient] = None,
    embedding_provider: Optional[EmbeddingProvider] = None,
) -> List[CaseResult]:
    embedding_provider = embedding_provider or DeterministicLocalEmbeddingProvider()

    with tempfile.TemporaryDirectory(prefix="eval-retrieval-") as tmp_dir:
        root = Path(tmp_dir)
        build_sample_repository(root)

        ingestion_result = IngestionService().ingest(str(root))
        parsing_result = ParsingService().parse_repository(str(root), ingestion_result)
        index_result = IndexingService(embedding_provider, vector_store).index_repository(str(root))

        if neo4j_client is not None:
            GraphBuilder(neo4j_client).build(str(root), ingestion_result, parsing_result)

        try:
            return [
                _evaluate_case(
                    case, vector_store, embedding_provider, neo4j_client, index_result.repository_id, str(root)
                )
                for case in RETRIEVAL_CASES
            ]
        finally:
            _cleanup_repository(vector_store, index_result.repository_id)
            if neo4j_client is not None:
                _cleanup_graph(neo4j_client, str(root.resolve()))
