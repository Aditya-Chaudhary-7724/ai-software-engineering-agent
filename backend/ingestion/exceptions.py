"""Exceptions raised by the ingestion subsystem."""


class IngestionError(Exception):
    """Base class for all ingestion-related errors."""


class InvalidRepositoryPathError(IngestionError):
    """Raised when the given repository path does not exist or is not a directory."""
