"""Production LLM provider, backed by the Anthropic Messages API.

REQUIRES EXTERNAL SERVICE / CREDENTIALS: reads LLM_API_KEY from the
environment and makes a real network call to Anthropic. Never
hardcode the key; it must come from the environment (see .env.example).
Not exercised against a live API in this build (no key is present in
this environment) — see tests/rag/test_anthropic_provider.py for the
mocked-client unit tests that verify request/response handling.
"""

import os
from typing import Optional

from anthropic import Anthropic

from vectorstore.exceptions import MissingCredentialsError

from rag.llm.base import LLMProvider

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 1024


class AnthropicLLMProvider(LLMProvider):
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        resolved_key = api_key or os.environ.get("LLM_API_KEY")
        if not resolved_key:
            raise MissingCredentialsError(
                "LLM_API_KEY is not set. Add a real Anthropic API key to your local "
                ".env to use AnthropicLLMProvider; it is never read from source code."
            )
        self._client = Anthropic(api_key=resolved_key)
        self._model = model
        self._max_tokens = max_tokens

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
            system=system or "",
        )
        return "".join(block.text for block in response.content if block.type == "text")
