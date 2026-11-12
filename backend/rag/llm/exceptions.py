"""Typed LLM failures, so callers can map each to a stable API error code
instead of a generic 502. Messages never contain a credential."""

from typing import Optional


class LLMError(Exception):
    """Base class for LLM provider failures."""


class LLMConfigurationError(LLMError):
    """LLM_PROVIDER (or a setting it needs) is missing or invalid. Raised
    before any network call; see backend/config.py."""


class LLMQuotaExceededError(LLMError):
    """The provider refused the request because a rate limit or free-tier
    quota was hit (HTTP 429). Never retried automatically: on a free tier a
    hidden retry spends quota the caller never sees."""

    def __init__(self, message: str, retry_after_seconds: Optional[int] = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class LLMEmptyResponseError(LLMError):
    """The provider returned no answer text, e.g. a reasoning model spent its
    whole completion budget on hidden reasoning. Reported, never returned as
    an empty answer."""
