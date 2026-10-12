"""Makes a recorded trace safe to return over HTTP.

Traces written by `JSONFileRecorder` (Phase 13) are already
secret-redacted, but they were designed for a LOCAL operator reading
them with `inspect_trace.py`, so they legitimately contain absolute
server paths: the trace-level `root_path` attribute, and span `error`
strings such as `NoTestCommandError: ... for '/private/var/.../repo'`
(observed in real trace files). Neither may reach a browser.

Applied in this order, on every string, at read time:
1. Drop keys that hold server paths by definition (`root_path`, ...).
2. Rewrite occurrences of this trace's own repository root
   (`<root>/src/app.py` -> `src/app.py`), keeping relative paths useful.
3. Replace any remaining absolute POSIX path with a placeholder.
4. Re-apply Phase 13's `sanitize_metadata`/`redact_text` (secret-shaped
   keys and values, length bounds) — reused, not reimplemented — as
   defense in depth for trace files written before a redaction fix.
"""

import re
from typing import Any, Dict, List, Optional

from observability.redaction import redact_text, sanitize_metadata

SERVER_PATH_PLACEHOLDER = "[server-path]"
REPOSITORY_ROOT_PLACEHOLDER = "[repository-root]"

SERVER_PATH_KEYS = frozenset({"root_path", "local_path", "repository_path", "workspace_root", "absolute_path"})

# A "/" that starts a path: not preceded by a word character, ".", "~",
# ":", "/", "\" or "-". This leaves relative paths ("src/app.py"), URLs
# ("https://github.com/a/b"), and ratios ("Fix attempt 1/2") untouched,
# while matching "/etc/passwd" or "'/private/var/x'" anywhere in a string.
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![\w.~:/\\-])/[^\s'\"`]+")


def scrub_text(value: str, known_roots: List[str]) -> str:
    for root in known_roots:
        value = value.replace(root.rstrip("/") + "/", "").replace(root, REPOSITORY_ROOT_PLACEHOLDER)
    value = _ABSOLUTE_PATH_PATTERN.sub(SERVER_PATH_PLACEHOLDER, value)
    return redact_text(value)


def _scrub_value(value: Any, known_roots: List[str]) -> Any:
    if isinstance(value, str):
        return scrub_text(value, known_roots)
    if isinstance(value, dict):
        return {
            str(key): _scrub_value(item, known_roots)
            for key, item in value.items()
            if str(key) not in SERVER_PATH_KEYS
        }
    if isinstance(value, list):
        return [_scrub_value(item, known_roots) for item in value]
    return value


def scrub_attributes(attributes: Optional[Dict[str, Any]], known_roots: List[str]) -> Dict[str, Any]:
    return sanitize_metadata(_scrub_value(attributes or {}, known_roots))


def known_roots_of(trace_attributes: Optional[Dict[str, Any]]) -> List[str]:
    root = (trace_attributes or {}).get("root_path")
    return [root] if isinstance(root, str) and root.startswith("/") and len(root) > 1 else []
