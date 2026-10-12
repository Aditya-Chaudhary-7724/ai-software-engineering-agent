"""Repository file tree and file content (read-only).

Reuses the Phase 8 tools (`list_files`, `read_file`) and their safety
boundary (`resolve_safe_path`, sensitive-file and size checks) rather
than a second implementation. The root path always comes from
`require_repository_on_disk` (a trusted, server-side lookup).

Additional HTTP-boundary checks, applied BEFORE the tool runs so each
failure maps to a stable error code instead of a message that echoes
the path:
- names are compared case-insensitively, because macOS filesystems are
  case-insensitive by default — `.ENV` opens the same file as `.env`,
  but `fnmatch` on POSIX would not match it against the `.env` pattern;
- files inside ingestion's ignored directories (`.git`, `node_modules`,
  ...) are refused, keeping content access consistent with the tree
  listing (which never shows them) — `.git/config` can hold credentials
  embedded in a remote URL.
"""

from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, Query

from ingestion.filters import DEFAULT_IGNORED_DIR_NAMES, DEFAULT_MAX_FILE_SIZE_BYTES, is_sensitive_filename
from ingestion.language import detect_language
from tools.exceptions import ToolAuthorizationError, ToolInputError
from tools.file_tools import list_files, read_file
from tools.schemas import ListFilesInput, ReadFileInput
from tools.security import resolve_safe_path

from api.dependencies import require_repository_on_disk
from api.errors import APIError
from api.repository_catalog import RepositoryRecord
from api.schemas import FileContent, FileEntry, FileTree

router = APIRouter(tags=["files"])

MAX_PATH_LENGTH = 1024
MAX_LISTED_FILES = 5000

_IGNORED_DIR_NAMES_LOWER = frozenset(name.lower() for name in DEFAULT_IGNORED_DIR_NAMES)


def _resolve_within(root_path: str, relative_path: str) -> Path:
    if "\x00" in relative_path:
        raise APIError(400, "path_not_allowed")
    try:
        return resolve_safe_path(root_path, relative_path)
    except (ToolAuthorizationError, ValueError, OSError):
        raise APIError(400, "path_not_allowed")


def _is_sensitive(name: str) -> bool:
    return is_sensitive_filename(name) or is_sensitive_filename(name.lower())


def _is_in_ignored_directory(relative_path: PurePosixPath) -> bool:
    return any(part.lower() in _IGNORED_DIR_NAMES_LOWER for part in relative_path.parts[:-1])


@router.get("/repositories/{repository_id}/files", response_model=FileTree)
def get_file_tree(
    directory: str = Query("", max_length=MAX_PATH_LENGTH),
    record: RepositoryRecord = Depends(require_repository_on_disk),
) -> FileTree:
    normalized = directory.strip("/")
    if normalized:
        _resolve_within(record.root_path, normalized)

    # list_files scans through IngestionService, so sensitive, binary,
    # oversized, and ignored-directory files never appear here at all.
    entries = list_files(ListFilesInput(root_path=record.root_path, directory=normalized)).files
    files = [
        FileEntry(relative_path=e.relative_path, language=e.language, category=e.category, size_bytes=e.size_bytes)
        for e in sorted(entries, key=lambda e: e.relative_path)
    ]
    return FileTree(
        repository_id=record.id,
        directory=normalized,
        files=files[:MAX_LISTED_FILES],
        total=len(files),
        truncated=len(files) > MAX_LISTED_FILES,
    )


@router.get("/repositories/{repository_id}/files/content", response_model=FileContent)
def get_file_content(
    path: str = Query(..., min_length=1, max_length=MAX_PATH_LENGTH),
    record: RepositoryRecord = Depends(require_repository_on_disk),
) -> FileContent:
    resolved = _resolve_within(record.root_path, path)
    relative = PurePosixPath(resolved.relative_to(Path(record.root_path).resolve()).as_posix())

    # Checked before existence, on both the requested and the resolved
    # name (a symlink can rename), so the response never reveals whether
    # a sensitive file exists.
    if _is_sensitive(PurePosixPath(path).name) or _is_sensitive(resolved.name) or _is_in_ignored_directory(relative):
        raise APIError(403, "file_access_denied")
    if not resolved.is_file():
        raise APIError(404, "file_not_found")

    size_bytes = resolved.stat().st_size
    if size_bytes > DEFAULT_MAX_FILE_SIZE_BYTES:
        raise APIError(413, "file_too_large")

    try:
        result = read_file(ReadFileInput(root_path=record.root_path, relative_path=str(relative)))
    except ToolAuthorizationError:
        raise APIError(403, "file_access_denied")
    except ToolInputError:
        # The only remaining ToolInputError after the checks above is a
        # non-UTF-8 (binary) file.
        raise APIError(415, "file_not_text")

    return FileContent(
        repository_id=record.id,
        relative_path=str(relative),
        language=detect_language(resolved.suffix),
        size_bytes=size_bytes,
        truncated=result.truncated,
        content=result.content,
    )
