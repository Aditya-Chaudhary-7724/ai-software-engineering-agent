"""Structured metadata models produced by repository ingestion.

These are plain, dependency-free dataclasses. There is no API layer yet
to justify a validation library (e.g. Pydantic); if a FastAPI boundary
is introduced in a later phase, these can be wrapped or replaced then.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class FileMetadata:
    """Metadata describing a single relevant file discovered during ingestion."""

    relative_path: str
    filename: str
    extension: str
    language: Optional[str]
    size_bytes: int
    category: str


@dataclass(frozen=True)
class IgnoredFile:
    """A file that was discovered but excluded, with the reason why."""

    relative_path: str
    reason: str


@dataclass
class LanguageStats:
    """Aggregate statistics for a single detected language."""

    file_count: int = 0
    total_bytes: int = 0


@dataclass
class RepositoryMetadata:
    """Repository-level summary produced by ingestion."""

    name: str
    root_path: str
    total_files_discovered: int
    relevant_files: int
    ignored_files: int
    language_stats: dict[str, LanguageStats] = field(default_factory=dict)


@dataclass
class IngestionResult:
    """The full structured output of an ingestion run."""

    repository: RepositoryMetadata
    files: list[FileMetadata]
    ignored_files: list[IgnoredFile]
    errors: list[str]
