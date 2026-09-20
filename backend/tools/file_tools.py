"""Read-only filesystem tools: list_files, read_file, analyze_code.

No write capability exists anywhere in this module, or anywhere in
this package — Phase 8 is explicitly read-only tools. Writing files
is Phase 9's concern and requires human approval (see agent/nodes.py's
human_approval_node), not a tool an agent can call directly.

Phase 14 hardening: `read_file`/`analyze_code` previously applied only
`resolve_safe_path` (path-traversal protection) — neither Phase 1
ingestion's size limit nor its sensitive-filename exclusion applied to
these DIRECT file-access tools, since they read straight from disk
rather than going through `IngestionService`. That meant an agent
could `read_file` a target repository's `.env` (unlike the RAG/LLM
path, which now excludes it at ingestion — see `ingestion/filters.py`),
or read an arbitrarily large file fully into memory before the
existing `READ_FILE_MAX_CHARS` truncation ever applied to the
*output*. Both tools now reuse the exact same, already-justified
`ingestion.filters` policy (never a second, divergent one) before
touching file content at all.
"""

from ingestion.filters import DEFAULT_MAX_FILE_SIZE_BYTES, is_sensitive_filename
from ingestion.language import detect_language
from ingestion.service import IngestionService
from parsing.python_parser import parse_python_file
from parsing.service import SUPPORTED_LANGUAGES
from parsing.treesitter_parser import parse_with_treesitter

from tools.exceptions import ToolAuthorizationError, ToolInputError
from tools.schemas import (
    AnalyzeCodeInput,
    AnalyzeCodeOutput,
    FileEntry,
    ListFilesInput,
    ListFilesOutput,
    ReadFileInput,
    ReadFileOutput,
)
from tools.security import resolve_safe_path

READ_FILE_MAX_CHARS = 100_000


def _reject_sensitive_or_oversized(path, relative_path: str) -> None:
    if is_sensitive_filename(path.name):
        raise ToolAuthorizationError(
            f"Refusing to access '{relative_path}': filename matches a known sensitive-credential pattern "
            "(see ingestion.filters.DEFAULT_SENSITIVE_FILE_PATTERNS)."
        )
    size_bytes = path.stat().st_size
    if size_bytes > DEFAULT_MAX_FILE_SIZE_BYTES:
        raise ToolInputError(
            f"Refusing to read '{relative_path}': {size_bytes} bytes exceeds the "
            f"{DEFAULT_MAX_FILE_SIZE_BYTES}-byte limit (checked before reading, not after)."
        )


def list_files(input_data: ListFilesInput) -> ListFilesOutput:
    """Scans the filesystem fresh on every call — a stale, previously
    indexed snapshot could disagree with what's actually on disk now.
    """
    result = IngestionService().ingest(input_data.root_path)

    prefix = input_data.directory.strip("/")
    files = [
        FileEntry(
            relative_path=f.relative_path, language=f.language, category=f.category, size_bytes=f.size_bytes
        )
        for f in result.files
        if not prefix or f.relative_path == prefix or f.relative_path.startswith(prefix + "/")
    ]
    return ListFilesOutput(files=files)


def read_file(input_data: ReadFileInput) -> ReadFileOutput:
    path = resolve_safe_path(input_data.root_path, input_data.relative_path)

    if not path.exists():
        raise ToolInputError(f"File not found: {input_data.relative_path}")
    if not path.is_file():
        raise ToolInputError(f"Not a file: {input_data.relative_path}")

    _reject_sensitive_or_oversized(path, input_data.relative_path)

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ToolInputError(f"Cannot read '{input_data.relative_path}': not a UTF-8 text file (likely binary).")

    truncated = len(content) > READ_FILE_MAX_CHARS
    if truncated:
        content = content[:READ_FILE_MAX_CHARS]

    return ReadFileOutput(relative_path=input_data.relative_path, content=content, truncated=truncated)


def analyze_code(input_data: AnalyzeCodeInput) -> AnalyzeCodeOutput:
    path = resolve_safe_path(input_data.root_path, input_data.relative_path)

    if not path.exists() or not path.is_file():
        raise ToolInputError(f"File not found: {input_data.relative_path}")

    _reject_sensitive_or_oversized(path, input_data.relative_path)

    language = detect_language(path.suffix)
    if language is None:
        raise ToolInputError(f"No language detected for extension '{path.suffix}'.")
    if language not in SUPPORTED_LANGUAGES:
        raise ToolInputError(f"No parser available for language '{language}'.")

    if language == "Python":
        try:
            source_text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ToolInputError(f"Cannot read '{input_data.relative_path}': not a UTF-8 text file.")
        parsed = parse_python_file(source_text, input_data.relative_path)
    else:
        raw_bytes = path.read_bytes()
        parsed = parse_with_treesitter(raw_bytes, input_data.relative_path, language)

    return AnalyzeCodeOutput(
        relative_path=parsed.relative_path,
        language=parsed.language,
        imports=[imp.module for imp in parsed.imports],
        classes=[cls.name for cls in parsed.classes],
        functions=[func.qualified_name for func in parsed.functions],
        parse_errors=parsed.parse_errors,
    )
