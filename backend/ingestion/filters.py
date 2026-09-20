"""Ignore/filter policy for discovered files.

This module contains pure decision logic only — no filesystem walking.
The scanner uses `DEFAULT_FILTER_CONFIG.ignored_dir_names` (or a custom
FilterConfig) to prune directory traversal; this module then decides,
file by file, whether a discovered file is relevant.

Policy, in order of precedence:
1. Filename matches a known sensitive/credential-file pattern (`.env`,
   `*.pem`, `id_rsa`, ...) -> ignored (Phase 14 — see
   `is_sensitive_filename` and docs/security.md). Checked first,
   deliberately: this reason must never be masked by a coincidental
   match on a different category.
2. Filename matches a known generated-artifact glob pattern -> ignored.
3. Extension matches a known binary extension -> ignored.
4. File size exceeds the configured limit -> ignored.
5. Extension is unrecognized: sniff the first KB for a null byte to
   guess whether it is binary -> ignored if so.
6. Otherwise: relevant.

Unknown extensions are never ignored purely for being unknown — only
after the content sniff suggests binary content. This avoids silently
dropping legitimate source files written in languages Phase 1 doesn't
yet have a language mapping for.
"""

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DEFAULT_IGNORED_DIR_NAMES = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".next",
        "dist",
        "build",
        "coverage",
        ".cache",
        ".pytest_cache",
        ".mypy_cache",
        ".idea",
        ".vscode",
    }
)

DEFAULT_IGNORED_FILE_PATTERNS = (
    "*.pyc",
    "*.pyo",
    "*.class",
    "*.o",
    "*.obj",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.exe",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.log",
    "*.tmp",
    "*.temp",
    "*.swp",
)

DEFAULT_SENSITIVE_FILE_PATTERNS = (
    # Phase 14: repository content is untrusted input, and a target
    # repository may (accidentally or otherwise) contain committed
    # secrets. Excluding these at ingestion — the earliest point in the
    # pipeline — means they never reach chunking, embedding, retrieval,
    # or the LLM prompt, and never get returned by the read_file/
    # analyze_code tools either (see tools/file_tools.py, which reuses
    # `is_sensitive_filename` below). Filename-based detection is a
    # heuristic, not a guarantee — see docs/security.md for what this
    # does and doesn't cover.
    ".env",
    ".env.*",  # deliberately also excludes .env.example's real-looking siblings; see docs/security.md
    "*.pem",
    "*.key",
    "*.pfx",
    "*.p12",
    "*.keystore",
    "*.jks",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials.json",
    "secrets.json",
    ".npmrc",
    ".netrc",
    "*.kdbx",
)

DEFAULT_BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".bmp",
        ".webp",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".7z",
        ".rar",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp3",
        ".mp4",
        ".mov",
        ".db",
        ".sqlite",
    }
)

DEFAULT_MAX_FILE_SIZE_BYTES = 1_000_000  # 1 MB, configurable per FilterConfig

_SNIFF_BYTES = 1024


@dataclass(frozen=True)
class FilterConfig:
    """Configurable ignore/filter rules. Later phases may adjust these."""

    ignored_dir_names: frozenset[str] = field(default_factory=lambda: DEFAULT_IGNORED_DIR_NAMES)
    ignored_file_patterns: tuple[str, ...] = DEFAULT_IGNORED_FILE_PATTERNS
    binary_extensions: frozenset[str] = field(default_factory=lambda: DEFAULT_BINARY_EXTENSIONS)
    sensitive_file_patterns: tuple[str, ...] = DEFAULT_SENSITIVE_FILE_PATTERNS
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES


def is_ignored_directory(dir_name: str, config: FilterConfig) -> bool:
    """Whether a directory name should be excluded from traversal entirely."""
    return dir_name in config.ignored_dir_names


def _matches_ignored_pattern(filename: str, config: FilterConfig) -> bool:
    return any(fnmatch.fnmatch(filename, pattern) for pattern in config.ignored_file_patterns)


def is_sensitive_filename(filename: str, config: Optional[FilterConfig] = None) -> bool:
    """Whether a filename matches a known credential/secret-file
    pattern (`.env`, `*.pem`, `id_rsa`, ...). Exported so
    `tools/file_tools.py` can apply the exact same policy to direct
    file access (`read_file`/`analyze_code`), not just the ingestion
    scan — see that module for why both paths need it independently.
    """
    config = config or FilterConfig()
    return any(fnmatch.fnmatch(filename, pattern) for pattern in config.sensitive_file_patterns)


def _looks_binary_by_content(path: Path, sniff_bytes: int = _SNIFF_BYTES) -> bool:
    """Heuristic: a null byte in the first chunk strongly suggests binary content."""
    try:
        with path.open("rb") as handle:
            chunk = handle.read(sniff_bytes)
    except OSError:
        # If we can't even read it, treat as not-relevant rather than crash the run.
        return True
    return b"\x00" in chunk


def classify_file(
    path: Path, relative_path: str, size_bytes: int, config: FilterConfig
) -> tuple[bool, Optional[str]]:
    """Decide whether a discovered file is relevant.

    Returns (is_relevant, ignore_reason). ignore_reason is None when relevant.
    """
    filename = path.name
    extension = path.suffix.lower()

    if is_sensitive_filename(filename, config):
        return False, "sensitive_file"

    if _matches_ignored_pattern(filename, config):
        return False, "generated_artifact"

    if extension in config.binary_extensions:
        return False, "binary_extension"

    if size_bytes > config.max_file_size_bytes:
        return False, "file_too_large"

    if extension not in _KNOWN_TEXT_LIKELY_EXTENSIONS and _looks_binary_by_content(path):
        return False, "binary_content"

    return True, None


# Extensions we already trust to be text based on the language map / common
# config formats, so we skip the content sniff for them (pure optimization —
# correctness does not depend on this set being complete).
_KNOWN_TEXT_LIKELY_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".cc",
        ".cxx",
        ".hpp",
        ".go",
        ".html",
        ".htm",
        ".css",
        ".sql",
        ".json",
        ".yaml",
        ".yml",
        ".md",
        ".rst",
        ".txt",
        ".toml",
        ".ini",
        ".cfg",
    }
)
