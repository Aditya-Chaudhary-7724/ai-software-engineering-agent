"""Parsing orchestration: takes a Phase 1 IngestionResult and produces
structured symbol metadata for every source file it can parse.

Only files Phase 1 classified as category "source" are attempted.
Among those, SQL currently has no entity extractor (functions/classes
do not map cleanly onto SQL) and is recorded as skipped rather than
silently dropped.
"""

from pathlib import Path

from ingestion.models import IngestionResult
from parsing.languages import EXTRACTORS
from parsing.models import ParsedFile, ParsingResult
from parsing.python_parser import parse_python_file
from parsing.treesitter_parser import parse_with_treesitter

SUPPORTED_LANGUAGES = frozenset({"Python"} | set(EXTRACTORS.keys()))


class ParsingService:
    """Orchestrates parsing of every source file discovered by ingestion."""

    def parse_repository(self, repository_root: str, ingestion_result: IngestionResult) -> ParsingResult:
        root = Path(repository_root)
        result = ParsingResult()

        for file_meta in ingestion_result.files:
            if file_meta.category != "source":
                continue

            if file_meta.language not in SUPPORTED_LANGUAGES:
                result.skipped_files.append(
                    (file_meta.relative_path, f"unsupported_language:{file_meta.language}")
                )
                continue

            absolute_path = root / file_meta.relative_path

            try:
                raw_bytes = absolute_path.read_bytes()
            except OSError as exc:
                result.errors.append(f"Cannot read '{file_meta.relative_path}': {exc}")
                continue

            parsed_file = self._parse_file(raw_bytes, file_meta.relative_path, file_meta.language)
            result.parsed_files.append(parsed_file)

        return result

    @staticmethod
    def _parse_file(raw_bytes: bytes, relative_path: str, language: str) -> ParsedFile:
        if language == "Python":
            try:
                source_text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                parsed = ParsedFile(relative_path=relative_path, language=language)
                parsed.parse_errors.append(f"Cannot decode as UTF-8: {exc}")
                return parsed
            return parse_python_file(source_text, relative_path)

        return parse_with_treesitter(raw_bytes, relative_path, language)
