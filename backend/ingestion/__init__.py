"""Repository ingestion subsystem (Phase 1).

Scans a local repository path, applies ignore/filter rules, detects
languages, and produces a structured IngestionResult. Does not read,
execute, or interpret repository contents beyond what is needed to
classify and describe files.
"""

from ingestion.exceptions import IngestionError, InvalidRepositoryPathError
from ingestion.models import (
    FileMetadata,
    IgnoredFile,
    IngestionResult,
    LanguageStats,
    RepositoryMetadata,
)
from ingestion.service import IngestionService

__all__ = [
    "IngestionError",
    "InvalidRepositoryPathError",
    "FileMetadata",
    "IgnoredFile",
    "IngestionResult",
    "LanguageStats",
    "RepositoryMetadata",
    "IngestionService",
]
