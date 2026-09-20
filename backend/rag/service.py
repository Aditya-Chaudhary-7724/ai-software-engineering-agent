"""RAG orchestration: question -> retrieval -> ranking -> context -> LLM -> answer.

Ties Phase 3 (vector store) together with keyword search, ranking, and
an LLM provider into one entry point.
"""

from typing import Optional

from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.store import VectorStore

from rag.context import DEFAULT_MAX_CONTEXT_CHARS, build_context
from rag.keyword_search import keyword_search
from rag.llm.base import LLMProvider
from rag.models import RAGAnswer, Source
from rag.prompt import SYSTEM_PROMPT, build_prompt
from rag.ranking import merge_and_rank

DEFAULT_VECTOR_TOP_K = 10
DEFAULT_KEYWORD_TOP_K = 10
DEFAULT_FINAL_TOP_K = 5


class RAGService:
    def __init__(
        self,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        llm_provider: LLMProvider,
        vector_top_k: int = DEFAULT_VECTOR_TOP_K,
        keyword_top_k: int = DEFAULT_KEYWORD_TOP_K,
        final_top_k: int = DEFAULT_FINAL_TOP_K,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    ) -> None:
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider
        self._llm_provider = llm_provider
        self._vector_top_k = vector_top_k
        self._keyword_top_k = keyword_top_k
        self._final_top_k = final_top_k
        self._max_context_chars = max_context_chars

    def answer(
        self,
        question: str,
        repository_id: Optional[int] = None,
        language: Optional[str] = None,
        chunk_type: Optional[str] = None,
    ) -> RAGAnswer:
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
        finally:
            conn.close()

        candidates = merge_and_rank(vector_hits, keyword_hits, top_k=self._final_top_k)
        context_text, included = build_context(candidates, max_chars=self._max_context_chars)

        prompt = build_prompt(question, context_text)
        answer_text = self._llm_provider.generate(prompt, system=SYSTEM_PROMPT)

        sources = [
            Source(
                relative_path=c.relative_path,
                start_line=c.start_line,
                end_line=c.end_line,
                chunk_type=c.chunk_type,
                symbol_name=c.symbol_name,
            )
            for c in included
        ]

        return RAGAnswer(
            question=question,
            answer=answer_text,
            sources=sources,
            context_chunk_count=len(included),
        )
