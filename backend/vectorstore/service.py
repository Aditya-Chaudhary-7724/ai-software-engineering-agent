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
from vectorstore.store import VectorStore, embedding_column


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

        # Fail fast, before spending time embedding, if this provider's
        # vectors have no column to go into.
        embedding_column(self._embedding_provider.dimension)
        embeddings = self._embedding_provider.embed([c.content for c in chunks]) if chunks else []

        conn = self._vector_store.connect()
        try:
            apply_schema(conn)  # commits on its own; no transaction is open afterwards
            # One transaction: upsert the repository row, delete ONLY that
            # repository's existing chunks, insert the new ones. Readers see
            # either the complete old index or the complete new one; any
            # failure rolls everything back (including a brand-new repository
            # row). The upsert row-locks the repository until commit, so two
            # concurrent re-indexes of the same repository run one after the
            # other instead of both inserting a full copy.
            with conn.transaction():
                # The model is recorded in the SAME transaction that replaces the
                # chunks, so the recorded model always matches the stored vectors.
                repository_id = self._vector_store.get_or_create_repository(
                    conn,
                    ingestion_result.repository.name,
                    ingestion_result.repository.root_path,
                    embedding_model=self._embedding_provider.model_id,
                    commit=False,
                )
                self._vector_store.delete_repository_chunks(conn, repository_id)
                self._vector_store.insert_chunks(conn, repository_id, chunks, embeddings, commit=False)
        finally:
            conn.close()

        return IndexingResult(repository_id=repository_id, chunks_indexed=len(chunks))
