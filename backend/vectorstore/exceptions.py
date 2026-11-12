"""Exceptions raised by the vector store / embeddings subsystem."""


class VectorStoreError(Exception):
    """Base class for all vector-store-related errors."""


class MissingCredentialsError(VectorStoreError):
    """Raised when an embedding provider needs an API key that isn't set."""


class EmbeddingConfigurationError(VectorStoreError):
    """EMBEDDING_PROVIDER (or a setting it needs) is missing or invalid, or
    the provider's package is not installed. See backend/config.py."""


class EmbeddingDimensionError(VectorStoreError):
    """A provider produced vectors of a different dimension than it declared.
    Raised instead of storing or searching with them."""
