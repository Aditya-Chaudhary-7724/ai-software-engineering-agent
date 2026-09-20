"""Finds the file most relevant to a modification instruction.

Reuses Phase 4's vector+keyword retrieval and ranking exactly as-is —
"find the file affected by this request" is the same problem as
"find the code relevant to this question", just consumed differently
(top-1 file path instead of a context string for an LLM).
"""

from typing import Optional

from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.store import VectorStore

from rag.keyword_search import keyword_search
from rag.ranking import merge_and_rank


def find_affected_file(
    vector_store: VectorStore, embedding_provider: EmbeddingProvider, repository_id: int, instruction: str
) -> Optional[str]:
    conn = vector_store.connect()
    try:
        query_embedding = embedding_provider.embed([instruction])[0]
        vector_hits = vector_store.similarity_search(conn, query_embedding, top_k=5, repository_id=repository_id)
        keyword_hits = keyword_search(conn, instruction, top_k=5, repository_id=repository_id)
    finally:
        conn.close()

    ranked = merge_and_rank(vector_hits, keyword_hits, top_k=1)
    return ranked[0].relative_path if ranked else None
