"""Hybrid retrieval orchestration: vector + keyword + graph evidence,
merged into one ranked context, then answered by an LLM.

Architectural seam, documented rather than hidden: Phase 3 (PostgreSQL)
identifies a repository by an integer `repository_id`, while Phase 5
(Neo4j) identifies it by `root_path` — the two subsystems were built
independently and don't share one repository identifier yet. This
service requires the caller to supply both. Unifying them (e.g.
storing `repository_id` as a property on the Neo4j Repository node)
is a reasonable future refinement, not implemented here since it would
mean reopening already-tested Phase 3/5 code for a convenience, not a
correctness fix.

Graph expansion is bounded by construction, not by a retry limit: it
runs exactly one hop out from a capped number of seed candidates
(`seed_limit`), so there's no risk of unbounded traversal regardless of
repository size.
"""

from typing import Optional

from graph.client import Neo4jClient
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.store import VectorStore

from rag.context import DEFAULT_MAX_CONTEXT_CHARS, build_context
from rag.keyword_search import keyword_search
from rag.llm.base import LLMProvider
from rag.prompt import SYSTEM_PROMPT, build_prompt
from rag.ranking import merge_and_rank as merge_seed_candidates

from hybrid.graph_expansion import build_graph_candidates
from hybrid.models import HybridAnswer, HybridSource
from hybrid.ranking import merge_and_rank

DEFAULT_VECTOR_TOP_K = 10
DEFAULT_KEYWORD_TOP_K = 10
DEFAULT_SEED_LIMIT = 5
DEFAULT_FINAL_TOP_K = 5


class HybridRAGService:
    def __init__(
        self,
        vector_store: VectorStore,
        graph_client: Neo4jClient,
        embedding_provider: EmbeddingProvider,
        llm_provider: LLMProvider,
        vector_top_k: int = DEFAULT_VECTOR_TOP_K,
        keyword_top_k: int = DEFAULT_KEYWORD_TOP_K,
        seed_limit: int = DEFAULT_SEED_LIMIT,
        final_top_k: int = DEFAULT_FINAL_TOP_K,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    ) -> None:
        self._vector_store = vector_store
        self._graph_client = graph_client
        self._embedding_provider = embedding_provider
        self._llm_provider = llm_provider
        self._vector_top_k = vector_top_k
        self._keyword_top_k = keyword_top_k
        self._seed_limit = seed_limit
        self._final_top_k = final_top_k
        self._max_context_chars = max_context_chars

    def answer(
        self,
        question: str,
        repository_id: int,
        root_path: str,
        language: Optional[str] = None,
        chunk_type: Optional[str] = None,
    ) -> HybridAnswer:
        conn = self._vector_store.connect()
        try:
            query_embedding = self._embedding_provider.embed([question])[0]
            vector_hits = self._vector_store.similarity_search(
                conn,
                query_embedding,
                top_k=self._vector_top_k,
                repository_id=repository_id,
                language=language,
                chunk_type=chunk_type,
            )
            keyword_hits = keyword_search(
                conn,
                question,
                top_k=self._keyword_top_k,
                repository_id=repository_id,
                language=language,
                chunk_type=chunk_type,
            )

            seeds = merge_seed_candidates(vector_hits, keyword_hits, top_k=self._seed_limit)
            graph_hits = build_graph_candidates(self._graph_client, conn, repository_id, root_path, seeds)
        finally:
            conn.close()

        candidates = merge_and_rank(vector_hits, keyword_hits, graph_hits, top_k=self._final_top_k)
        context_text, included = build_context(candidates, max_chars=self._max_context_chars)

        prompt = build_prompt(question, context_text)
        answer_text = self._llm_provider.generate(prompt, system=SYSTEM_PROMPT)

        sources = [
            HybridSource(
                relative_path=c.relative_path,
                start_line=c.start_line,
                end_line=c.end_line,
                chunk_type=c.chunk_type,
                symbol_name=c.symbol_name,
                found_via=c.found_via,
            )
            for c in included
        ]

        return HybridAnswer(
            question=question,
            answer=answer_text,
            sources=sources,
            context_chunk_count=len(included),
        )
