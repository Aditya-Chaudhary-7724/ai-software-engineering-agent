"""Indexing orchestration: ingest -> parse -> chunk -> embed -> store.

Ties Phases 1-3 together into one entry point: given a local
repository path, produce a fully indexed, searchable representation of
it in PostgreSQL + pgvector.
"""

from dataclasses import dataclass

from ingestion.service import IngestionService
from parsing.service import ParsingService
from vectorstore.chunking import build_chunks
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.migrations import apply_schema
from vectorstore.store import VectorStore


@dataclass(frozen=True)
class IndexingResult:
    repository_id: int
    chunks_indexed: int


class IndexingService:
    def __init__(self, embedding_provider: EmbeddingProvider, vector_store: VectorStore) -> None:
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store

    def index_repository(self, repository_path: str) -> IndexingResult:
        ingestion_result = IngestionService().ingest(repository_path)
        parsing_result = ParsingService().parse_repository(repository_path, ingestion_result)
        chunks = build_chunks(repository_path, ingestion_result, parsing_result)

        embeddings = self._embedding_provider.embed([c.content for c in chunks]) if chunks else []

        conn = self._vector_store.connect()
        try:
            apply_schema(conn)
            repository_id = self._vector_store.get_or_create_repository(
                conn, ingestion_result.repository.name, ingestion_result.repository.root_path
            )
            self._vector_store.insert_chunks(conn, repository_id, chunks, embeddings)
        finally:
            conn.close()

        return IndexingResult(repository_id=repository_id, chunks_indexed=len(chunks))
