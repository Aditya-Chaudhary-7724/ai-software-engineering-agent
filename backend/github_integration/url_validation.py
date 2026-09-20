"""Validates and normalizes GitHub repository URLs.

Deliberately narrow scope: only `https://github.com/<owner>/<repo>`
(optionally with a `.git` suffix or trailing slash) is accepted. SSH
URLs (`git@github.com:owner/repo.git`), GitHub Enterprise hosts, and
any other host are explicitly rejected rather than approximated — the
same "implement the mechanism honestly, don't fake the capability"
pattern as Phase 8's `get_callers`/`get_callees`. This is also what
makes the security property straightforward to reason about: the owner
and repo segments are validated against a strict character set before
they are ever used to build a filesystem path (workspace.py) or an API
request (api_client.py), so neither can smuggle a path-traversal
sequence, a shell metacharacter, or a second URL.
"""

import re
import unicodedata
from urllib.parse import urlparse

from github_integration.exceptions import InvalidRepositoryURLError
from github_integration.models import RepositoryReference

# GitHub's own rules are looser (e.g. usernames can't start/end with a
# hyphen, repo names allow more punctuation) — this is deliberately a
# strict subset: it rejects some technically-valid GitHub names, never
# accepts a filesystem- or shell-meaningful character (`/`, `..`, `~`,
# whitespace, quotes, etc.), which is the property this validator
# actually needs to guarantee.
_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


def _has_control_character(value: str) -> bool:
    # Phase 14: found by adversarial testing (tests/security/test_github_security.py)
    # that CPython's own `urlparse` silently STRIPS \t/\r/\n from a URL
    # before parsing it (an undocumented-to-most-callers CPython hardening
    # for header-injection-style bugs), which let a URL containing an
    # embedded newline through as if the newline had never been there —
    # e.g. "repo\nevil" silently became the harmless "repoevil". Nothing
    # downstream was actually reachable through that (the stripped
    # character never reaches git/HTTP), but a validator whose safety
    # depends on an undocumented quirk of a library it happens to call is
    # not trustworthy on inspection — reject control characters explicitly
    # and up front instead of relying on it.
    return any(unicodedata.category(ch) == "Cc" for ch in value)


def parse_github_url(url: str) -> RepositoryReference:
    if not isinstance(url, str) or not url.strip():
        raise InvalidRepositoryURLError("Repository URL must be a non-empty string.")

    if _has_control_character(url):
        raise InvalidRepositoryURLError(f"URL contains a control character and was refused: {url!r}")

    parsed = urlparse(url.strip())

    if parsed.params:
        # Phase 14: the legacy "path;params" URL syntax (RFC 2396) would
        # otherwise let a URL like ".../owner/repo;anything" through with
        # ";anything" silently discarded rather than the whole URL being
        # rejected as malformed — found by the same adversarial test.
        raise InvalidRepositoryURLError(f"URL contains unsupported path parameters and was refused: {url!r}")

    if parsed.scheme != "https":
        raise InvalidRepositoryURLError(
            f"Only https:// GitHub URLs are supported (got scheme {parsed.scheme!r}): {url!r}"
        )
    if parsed.netloc.lower() != "github.com":
        raise InvalidRepositoryURLError(f"Only github.com URLs are supported (got {parsed.netloc!r}): {url!r}")

    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]

    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        raise InvalidRepositoryURLError(
            f"URL must be of the form https://github.com/<owner>/<repo>: {url!r}"
        )

    owner, repo = parts
    if not (_SEGMENT_PATTERN.match(owner) and _SEGMENT_PATTERN.match(repo)):
        raise InvalidRepositoryURLError(f"Invalid owner/repo segment in URL: {url!r}")

    return RepositoryReference(owner=owner, repo=repo, url=f"https://github.com/{owner}/{repo}")
