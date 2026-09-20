"""A deterministic, non-LLM stand-in for LLMProvider.

NOT a real language model and must never be presented as one. It
exists solely so the retrieval -> ranking -> context -> "LLM" ->
grounded response pipeline can be exercised end-to-end in tests and
local development without an LLM_API_KEY. It performs no reasoning: it
just reports how much context it was given.
"""

from typing import Optional

from rag.llm.base import LLMProvider


class StubLLMProvider(LLMProvider):
    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        if "No relevant code context was found" in prompt:
            return "No relevant code context was found in the repository for this question."
        return (
            "[stub-llm] This is a deterministic placeholder response, not a real answer. "
            f"It was given a prompt of {len(prompt)} characters of retrieved code context."
        )
