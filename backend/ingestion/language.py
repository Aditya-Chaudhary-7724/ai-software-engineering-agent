"""Deterministic, extension-based language and category detection.

Limitations (Phase 1, documented intentionally rather than worked around):
- Detection is purely by file extension. A file's actual contents are
  never inspected to determine its language.
- Extensionless files (e.g. "Dockerfile", "Makefile") are not detected
  and will have language=None. Special-casing these is deferred until
  a later phase actually needs it.
- A single extension maps to exactly one language (e.g. ".h" -> "C"),
  even though it is ambiguous in reality (C vs C++ headers). This is an
  accepted simplification for Phase 1.
"""

from typing import Optional

EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JSX",
    ".ts": "TypeScript",
    ".tsx": "TSX",
    ".java": "Java",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".go": "Go",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".sql": "SQL",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
}

# Extensions with no dedicated "language" but a useful, documented category.
EXTENSION_CATEGORY_OVERRIDES: dict[str, str] = {
    ".md": "documentation",
    ".rst": "documentation",
    ".txt": "documentation",
    ".toml": "config",
    ".ini": "config",
    ".cfg": "config",
}

SOURCE_LANGUAGES = frozenset(
    {"Python", "JavaScript", "JSX", "TypeScript", "TSX", "Java", "C", "C++", "Go", "SQL"}
)
MARKUP_LANGUAGES = frozenset({"HTML", "CSS"})
CONFIG_LANGUAGES = frozenset({"JSON", "YAML"})


def detect_language(extension: str) -> Optional[str]:
    """Return the detected language for a file extension, or None if unknown."""
    return EXTENSION_LANGUAGE_MAP.get(extension.lower())


def categorize(extension: str, language: Optional[str]) -> str:
    """Return a coarse file category based on detected language or extension."""
    if language in SOURCE_LANGUAGES:
        return "source"
    if language in MARKUP_LANGUAGES:
        return "markup"
    if language in CONFIG_LANGUAGES:
        return "config"
    return EXTENSION_CATEGORY_OVERRIDES.get(extension.lower(), "other")
