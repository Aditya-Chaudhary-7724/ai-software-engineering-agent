"""Exceptions raised by the vector store / embeddings subsystem."""


class VectorStoreError(Exception):
    """Base class for all vector-store-related errors."""


class MissingCredentialsError(VectorStoreError):
    """Raised when an embedding provider needs an API key that isn't set."""
