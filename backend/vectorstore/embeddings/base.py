"""Embedding provider abstraction.

Every provider (real or local/test) implements the same interface so
the rest of the system — chunking, storage, retrieval — never depends
on which embedding model actually produced the vectors.
"""

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    dimension: int
    # Identifies the MODEL that produced the vectors (not just the vendor):
    # recorded on every indexed repository, and searches refuse to compare a
    # query embedded by one model with vectors from another
    # (VectorStore.similarity_search -> ReindexRequiredError). Two different
    # models must never share an id.
    model_id: str

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, in the same order."""
