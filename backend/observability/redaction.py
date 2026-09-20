"""Redaction/sanitization applied to every piece of structured data
before it reaches a recorder, an exporter, or a log line — the last
line of defense against a secret or a large source-code blob ending up
somewhere it shouldn't.

Two independent techniques, both applied:
1. KEY-based redaction: any attribute whose KEY name has a WHOLE TOKEN
   (split on `_`/`-`/camelCase boundaries, never a raw substring — see
   `_is_sensitive_key`'s own docstring for why that distinction matters)
   matching a small, deliberately broad list ("token", "key", "secret",
   "password", "authorization", "credential", ...) has its VALUE
   replaced with a fixed marker, regardless of what the value actually
   is. This is the primary defense, since this project's code already
   names credential-carrying fields predictably (`GITHUB_TOKEN`,
   `api_key`, `LLM_API_KEY`, `NEO4J_PASSWORD`, ...).
2. VALUE-based redaction: even under an innocuous-looking key, a
   string VALUE that matches a known secret SHAPE (a GitHub token
   prefix, a `Bearer `/`Authorization:` header, an OpenAI/Anthropic-style
   `sk-...` key, a database URL with a password embedded in it) is
   redacted too — defense in depth against a secret ending up
   somewhere the key-based check didn't anticipate.

Plus: every string value is length-bounded (never the full text) —
this is what "do not log the user's entire source code unnecessarily"
means in practice here, applied uniformly rather than trusting every
call site to remember to truncate itself.

Never raises: a sanitizer that itself crashes on unexpected input would
defeat "tracing must never break the agent" — anything it can't handle
is coerced to a safe placeholder instead of propagating an exception.
"""

import re
from typing import Any, Dict

REDACTED_MARKER = "***REDACTED***"

_SENSITIVE_KEY_TOKENS = frozenset(
    {
        "token",
        "key",
        "secret",
        "password",
        "passwd",
        "authorization",
        "credential",
        "credentials",
        "apikey",
        "accesskey",
        "private",
    }
)

_KEY_TOKEN_PATTERN = re.compile(r"[^a-zA-Z0-9]+")
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_SECRET_VALUE_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens: ghp_, gho_, ghu_, ghs_, ghr_
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI/Anthropic-style secret keys
    re.compile(r"[Bb]earer\s+[A-Za-z0-9._-]{10,}"),  # "Bearer <token>" header values
    re.compile(r"postgresql(\+\w+)?://[^/\s:]*:[^/\s@]+@"),  # a DB URL with an embedded password
)

_MAX_STRING_LENGTH = 500
_MAX_LIST_ITEMS = 20


def _is_sensitive_key(key: str) -> bool:
    """Whole-TOKEN matching, not raw substring containment: an earlier
    version of this check flagged `keyword_hit_count` as sensitive
    purely because "key" is a substring of "keyword" — a real bug
    (verified against a real trace — see tests/observability/test_redaction.py
    for the regression test). Splitting on non-alphanumeric characters
    and camelCase boundaries first, then checking each TOKEN against
    the sensitive list exactly, avoids that class of false positive
    while still catching `GITHUB_TOKEN`, `api_key`, `apiKey`, etc.
    """
    with_word_boundaries = _CAMEL_CASE_BOUNDARY.sub("_", key)
    tokens = [t for t in _KEY_TOKEN_PATTERN.split(with_word_boundaries.lower()) if t]
    return any(token in _SENSITIVE_KEY_TOKENS for token in tokens)


def redact_text(value: str) -> str:
    """Value-based secret redaction plus length bounding for one string."""
    try:
        redacted = value
        for pattern in _SECRET_VALUE_PATTERNS:
            redacted = pattern.sub(REDACTED_MARKER, redacted)
        if len(redacted) > _MAX_STRING_LENGTH:
            omitted = len(value) - _MAX_STRING_LENGTH
            redacted = redacted[:_MAX_STRING_LENGTH] + f"... [truncated, {omitted} more characters]"
        return redacted
    except Exception:
        return "<unrepresentable string>"


def sanitize_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively sanitizes a plain dict of attributes before it's
    attached to a span/trace or written to a recorder/exporter/logger.
    Safe to call on arbitrary, possibly-malformed structured data.
    """
    if not isinstance(data, dict):
        return {}

    sanitized: Dict[str, Any] = {}
    for key, value in data.items():
        try:
            key_str = str(key)
            if _is_sensitive_key(key_str):
                sanitized[key_str] = REDACTED_MARKER
            elif isinstance(value, str):
                sanitized[key_str] = redact_text(value)
            elif isinstance(value, dict):
                sanitized[key_str] = sanitize_metadata(value)
            elif isinstance(value, (list, tuple)):
                sanitized[key_str] = [
                    sanitize_metadata(item)
                    if isinstance(item, dict)
                    else (redact_text(item) if isinstance(item, str) else item)
                    for item in list(value)[:_MAX_LIST_ITEMS]
                ]
            elif isinstance(value, (int, float, bool)) or value is None:
                sanitized[key_str] = value
            else:
                # Anything else (a Path, a custom object, ...) is stringified
                # and still passed through value-based redaction/truncation —
                # never stored as an opaque, unserializable object.
                sanitized[key_str] = redact_text(str(value))
        except Exception:
            sanitized[str(key)] = "<unrepresentable value>"
    return sanitized
