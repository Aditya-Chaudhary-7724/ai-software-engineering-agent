"""Embedding provider abstraction.

Every provider (real or local/test) implements the same interface so
the rest of the system — chunking, storage, retrieval — never depends
on which embedding model actually produced the vectors.
"""

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    dimension: int

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, in the same order."""
