"""Ingestion orchestration.

Wires together scan -> filter -> language detection -> metadata
extraction -> structured result. Contains no low-level scanning,
filtering, or detection logic of its own.
"""

from pathlib import Path
from typing import Optional

from ingestion.exceptions import InvalidRepositoryPathError
from ingestion.filters import FilterConfig, classify_file
from ingestion.language import categorize, detect_language
from ingestion.models import (
    FileMetadata,
    IgnoredFile,
    IngestionResult,
    LanguageStats,
    RepositoryMetadata,
)
from ingestion.scanner import discover_files


class IngestionService:
    """Orchestrates ingestion of a single local repository path."""

    def __init__(self, filter_config: Optional[FilterConfig] = None) -> None:
        self.filter_config = filter_config or FilterConfig()

    def ingest(self, repository_path: str) -> IngestionResult:
        root = self._resolve_repository_root(repository_path)

        errors: list[str] = []
        discovered = discover_files(root, self.filter_config, errors)

        files: list[FileMetadata] = []
        ignored_files: list[IgnoredFile] = []
        language_stats: dict[str, LanguageStats] = {}

        for entry in discovered:
            try:
                size_bytes = entry.absolute_path.stat().st_size
            except OSError as exc:
                errors.append(f"Cannot stat '{entry.relative_path}': {exc}")
                continue

            is_relevant, ignore_reason = classify_file(
                entry.absolute_path, entry.relative_path, size_bytes, self.filter_config
            )

            if not is_relevant:
                assert ignore_reason is not None
                ignored_files.append(IgnoredFile(entry.relative_path, ignore_reason))
                continue

            language = detect_language(entry.absolute_path.suffix)
            category = categorize(entry.absolute_path.suffix, language)

            files.append(
                FileMetadata(
                    relative_path=entry.relative_path,
                    filename=entry.absolute_path.name,
                    extension=entry.absolute_path.suffix,
                    language=language,
                    size_bytes=size_bytes,
                    category=category,
                )
            )

            stats_key = language or "Unknown"
            stats = language_stats.setdefault(stats_key, LanguageStats())
            stats.file_count += 1
            stats.total_bytes += size_bytes

        repository = RepositoryMetadata(
            name=root.name,
            root_path=str(root),
            total_files_discovered=len(discovered),
            relevant_files=len(files),
            ignored_files=len(ignored_files),
            language_stats=language_stats,
        )

        return IngestionResult(
            repository=repository,
            files=files,
            ignored_files=ignored_files,
            errors=errors,
        )

    @staticmethod
    def _resolve_repository_root(repository_path: str) -> Path:
        root = Path(repository_path).expanduser().resolve()
        if not root.exists():
            raise InvalidRepositoryPathError(f"Path does not exist: {repository_path}")
        if not root.is_dir():
            raise InvalidRepositoryPathError(f"Path is not a directory: {repository_path}")
        return root
