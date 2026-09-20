"""Vector search foundation (Phase 3).

Chunks parsed code (Phase 2 output), embeds it via a pluggable
EmbeddingProvider, and stores/searches it in PostgreSQL + pgvector.
"""

from vectorstore.exceptions import MissingCredentialsError, VectorStoreError
from vectorstore.models import CodeChunk, SearchResult
from vectorstore.service import IndexingResult, IndexingService
from vectorstore.store import VectorStore

__all__ = [
    "VectorStoreError",
    "MissingCredentialsError",
    "CodeChunk",
    "SearchResult",
    "IndexingResult",
    "IndexingService",
    "VectorStore",
]
