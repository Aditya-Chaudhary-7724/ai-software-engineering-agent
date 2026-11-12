"""Exceptions raised by the vector store / embeddings subsystem."""

from typing import Optional


class VectorStoreError(Exception):
    """Base class for all vector-store-related errors."""


class MissingCredentialsError(VectorStoreError):
    """Raised when an embedding provider needs an API key that isn't set."""


class EmbeddingConfigurationError(VectorStoreError):
    """EMBEDDING_PROVIDER (or a setting it needs) is missing or invalid, or
    the provider's package is not installed. See backend/config.py."""


class ReindexRequiredError(VectorStoreError):
    """The repository's vectors were produced by a different embedding model
    than the one embedding the query (or by an unrecorded one, for indexes
    made before models were recorded). Comparing them would return
    meaningless matches, so the search is refused; re-indexing the
    repository with the current model fixes it."""

    def __init__(self, repository_id: int, indexed_with: Optional[str], active: str) -> None:
        super().__init__(
            f"repository {repository_id} was indexed with {indexed_with or 'an unrecorded model'}, "
            f"but queries are embedded with {active}: re-index it"
        )
        self.repository_id = repository_id
        self.indexed_with = indexed_with
        self.active = active


class SchemaOutOfDateError(VectorStoreError):
    """The database schema is older than this code expects (or was never
    applied). Code never applies it implicitly: run the explicit step."""

    def __init__(self, found: Optional[int], expected: int) -> None:
        state = "never applied" if found is None else f"version {found}"
        super().__init__(
            f"schema out of date ({state}, expected version {expected}): run backend/scripts/apply_schema.py"
        )
        self.found = found
        self.expected = expected


class EmbeddingDimensionError(VectorStoreError):
    """A provider produced vectors of a different dimension than it declared.
    Raised instead of storing or searching with them."""
