"""Best-effort resolution of import strings and base-class names to
concrete graph nodes, using only information Phase 1/2 actually extracted.

Explicitly NOT attempted (and explicitly not faked): Go and C/C++
import resolution. Go's project-internal import paths are prefixed
with the module's declared path from go.mod, which nothing in this
project parses yet; C/C++ `#include` local-vs-system distinction
(quoted vs angle-bracket) is lost in Phase 2's ImportEntity, so
resolution isn't attempted for either — an unresolved import simply
stays an unresolved Module node, which is honest, not incorrect.

Python limitation: relative imports (`from . import x`) are not
distinguished from absolute imports in Phase 2's extracted data (the
AST's relative-import `level` isn't captured), so this resolver always
treats Python import strings as absolute-from-repository-root.
Relative imports will typically fail to resolve — which just means no
RESOLVES_TO edge is created, not an incorrect one.
"""

import posixpath
from typing import Optional

from parsing.models import ParsingResult


def resolve_import_to_file(
    language: Optional[str], module: str, importing_file_relative_path: str, known_files: set[str]
) -> Optional[str]:
    if language == "Python":
        return _resolve_python(module, known_files)
    if language in ("JavaScript", "JSX", "TypeScript", "TSX"):
        return _resolve_js_like(module, importing_file_relative_path, known_files)
    if language == "Java":
        return _resolve_java(module, known_files)
    return None  # Go, C, C++, and anything else: not attempted (see module docstring)


def _resolve_python(module: str, known_files: set[str]) -> Optional[str]:
    """Phase 2's Python parser records `from X import Y` and `import X.Y`
    identically, as module="X.Y" (see python_parser.py) — there is no way
    to tell, from the extracted data alone, whether the last segment is a
    submodule or an imported name. Both interpretations are tried here:
    the full dotted path as a module (covers `import X.Y`), and the path
    with the last segment dropped (covers the more common `from X import Y`,
    where the real file is just X.py).
    """
    parts = module.split(".")
    candidate_bases = ["/".join(parts)]
    if len(parts) > 1:
        candidate_bases.append("/".join(parts[:-1]))

    for base in candidate_bases:
        for candidate in (f"{base}.py", f"{base}/__init__.py"):
            if candidate in known_files:
                return candidate
    return None


_JS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx")


def _resolve_js_like(module: str, importing_file_relative_path: str, known_files: set[str]) -> Optional[str]:
    if not module.startswith("."):
        return None  # bare specifier ("react", "fs") -> external package, not attempted

    import_dir = posixpath.dirname(importing_file_relative_path)
    raw_path = posixpath.normpath(posixpath.join(import_dir, module))

    candidates = [raw_path]
    candidates.extend(f"{raw_path}{ext}" for ext in _JS_EXTENSIONS)
    candidates.extend(posixpath.join(raw_path, f"index{ext}") for ext in _JS_EXTENSIONS)

    for candidate in candidates:
        if candidate in known_files:
            return candidate
    return None


def _resolve_java(module: str, known_files: set[str]) -> Optional[str]:
    if module.endswith(".*"):
        return None  # wildcard import: can't resolve to a single file
    candidate = module.replace(".", "/") + ".java"
    return candidate if candidate in known_files else None


def build_class_index(parsing_result: ParsingResult) -> dict[str, list[str]]:
    """Maps class name -> every file (relative_path) that defines a class
    with that name, across the whole repository. Used to resolve
    INHERITS edges — an edge is only created when a base-class name
    resolves unambiguously to exactly one file's class.
    """
    index: dict[str, list[str]] = {}
    for parsed_file in parsing_result.parsed_files:
        for cls in parsed_file.classes:
            index.setdefault(cls.name, []).append(parsed_file.relative_path)
    return index
