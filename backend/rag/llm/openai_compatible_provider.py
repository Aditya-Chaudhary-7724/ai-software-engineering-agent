"""LLM provider for any OpenAI-compatible Chat Completions API (Groq today).

Why OpenAI-compatible instead of a vendor SDK: Groq (and others, e.g.
Gemini's compatibility endpoint) accept the OpenAI request format, and the
`openai` SDK is already a dependency (embeddings), so this adds a provider
without adding a package. Selected by `rag.llm.factory`, never constructed
with a hardcoded key.

Design decisions:
- `max_retries=0`: the SDK retries 429s by default. On a free tier each
  hidden retry spends quota, so a 429 becomes `LLMQuotaExceededError`
  immediately and the caller decides what to tell the user.
- Only `message.content` is returned. Reasoning models (gpt-oss, qwen) may
  also return their reasoning in a separate field; it is never read, logged
  or shown (project rule: no hidden chain-of-thought in the UI). A `<think>`
  block that a model inlines into the content is stripped as well.
- `reasoning_effort` is passed only when configured: its allowed values
  differ per model family (Groq docs), so it is configuration, not code.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

import openai
from openai import OpenAI

from rag.llm.base import LLMProvider
from rag.llm.exceptions import LLMEmptyResponseError, LLMQuotaExceededError

DEFAULT_TIMEOUT_SECONDS = 60.0
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class LLMUsage:
    """Token counts the PROVIDER reported for one call (never estimated).
    `reasoning_tokens` is None when the provider does not break it out.
    Counts only: no prompt or response text."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    reasoning_tokens: Optional[int] = None


def _usage(response: Any) -> Optional[LLMUsage]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    details = getattr(usage, "completion_tokens_details", None)
    return LLMUsage(
        prompt_tokens=int(usage.prompt_tokens or 0),
        completion_tokens=int(usage.completion_tokens or 0),
        total_tokens=int(usage.total_tokens or 0),
        reasoning_tokens=getattr(details, "reasoning_tokens", None) if details is not None else None,
    )


def _retry_after_seconds(error: openai.RateLimitError) -> Optional[int]:
    """`retry-after` is standard HTTP but not documented by Groq: use it only
    when present and numeric."""
    response = getattr(error, "response", None)
    value = response.headers.get("retry-after") if response is not None else None
    try:
        return max(1, int(float(value))) if value is not None else None
    except ValueError:
        return None


class OpenAICompatibleLLMProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_completion_tokens: int,
        reasoning_effort: Optional[str] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=timeout_seconds)
        self.model = model
        self._max_completion_tokens = max_completion_tokens
        self._reasoning_effort = reasoning_effort
        # Usage of the most recent call, as reported by the provider (None if
        # it reported none, or the call failed). Read by budget accounting and
        # the smoke script; not thread-safe across concurrent calls on one
        # instance (each request/task builds its own provider).
        self.last_usage: Optional[LLMUsage] = None

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": self._max_completion_tokens,
        }
        if self._reasoning_effort:
            params["reasoning_effort"] = self._reasoning_effort
        self.last_usage = None
        try:
            response = self._client.chat.completions.create(**params)
        except openai.RateLimitError as exc:
            raise LLMQuotaExceededError("LLM provider rate limit or quota reached", _retry_after_seconds(exc)) from exc

        self.last_usage = _usage(response)
        choice = response.choices[0] if response.choices else None
        text = _THINK_BLOCK.sub("", (choice.message.content if choice else None) or "").strip()
        if not text:
            reason = getattr(choice, "finish_reason", None) if choice else "no_choices"
            raise LLMEmptyResponseError(f"LLM returned no answer text (finish_reason={reason})")
        return text
