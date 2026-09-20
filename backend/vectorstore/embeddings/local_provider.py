"""A deterministic, dependency-free, non-semantic embedding provider.

This is NOT a real embedding model and must never be used to claim or
demonstrate real retrieval quality. It exists solely so the storage,
indexing, and similarity-search pipeline can be exercised end-to-end
in tests and local development without network access or an
EMBEDDING_API_KEY. Semantically similar text does NOT produce similar
vectors here — vectors are derived purely from a hash of the input.
"""

import hashlib

from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.models import EMBEDDING_DIMENSION


class DeterministicLocalEmbeddingProvider(EmbeddingProvider):
    dimension = EMBEDDING_DIMENSION

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        while len(values) < self.dimension:
            for byte in digest:
                if len(values) == self.dimension:
                    break
                values.append((byte / 127.5) - 1.0)
        return values
