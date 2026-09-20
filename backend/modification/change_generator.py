"""Generates proposed new file content for a modification instruction.

REQUIRES A REAL LLM for meaningful output: with StubLLMProvider (no
LLM_API_KEY), this mechanically produces a diff and can prove the
propose -> diff -> approve -> apply pipeline works end-to-end, but the
"proposed content" is the stub's fixed placeholder text, not valid
code. Content *quality* requires AnthropicLLMProvider and a real key —
same caveat as Phase 4's answer generation.
"""

from rag.llm.base import LLMProvider

CHANGE_PROMPT_TEMPLATE = (
    "You are modifying a single source file in a repository. Return ONLY "
    "the complete new content of the file — no explanation, no markdown "
    "code fences, nothing else.\n\n"
    "File: {relative_path}\n"
    "Current content:\n"
    "{content}\n\n"
    "Requested change: {instruction}\n"
)


class ChangeGenerator:
    def __init__(self, llm_provider: LLMProvider) -> None:
        self._llm_provider = llm_provider

    def generate(self, relative_path: str, original_content: str, instruction: str) -> str:
        prompt = CHANGE_PROMPT_TEMPLATE.format(
            relative_path=relative_path, content=original_content, instruction=instruction
        )
        return self._llm_provider.generate(prompt)
