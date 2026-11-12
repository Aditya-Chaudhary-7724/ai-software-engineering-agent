"""Per-task token ceiling for public-demo agent tasks.

A demo task may make several LLM calls (answering, then generating the
changed file). BudgetedLLMProvider wraps the demo's provider for ONE task:

- before each call it computes the call's WORST case, i.e. tokens already
  used + an upper estimate of this prompt + the full completion allowance,
  and refuses the call (DemoTaskTokenLimitError) if that could exceed the
  task's ceiling, so the ceiling is never crossed rather than detected
  afterwards;
- after each call it adds the usage the provider REPORTED; if the provider
  reported none (e.g. the stub), it adds the worst case (never undercounts).

The executor ends a task that hits the ceiling with
`demo_task_token_limit_reached`, without storing any proposal, and settles
the day's budget to `used` whatever the outcome.
"""

import math
from typing import Optional

from rag.llm.base import LLMProvider

# Upper estimate of tokens per character: real tokenizers average about one
# token per 3-4 characters of English or code; 3 errs on the high side.
_CHARS_PER_TOKEN_UPPER_ESTIMATE = 3
_PER_MESSAGE_OVERHEAD_TOKENS = 16


class DemoTaskTokenLimitError(Exception):
    """The next call could exceed the demo task's token ceiling."""


def estimate_prompt_tokens(prompt: str, system: Optional[str]) -> int:
    chars = len(prompt) + len(system or "")
    messages = 2 if system else 1
    return math.ceil(chars / _CHARS_PER_TOKEN_UPPER_ESTIMATE) + messages * _PER_MESSAGE_OVERHEAD_TOKENS


class BudgetedLLMProvider(LLMProvider):
    def __init__(self, inner: LLMProvider, *, ceiling: int, max_completion_tokens: int) -> None:
        self._inner = inner
        self._ceiling = ceiling
        self._max_completion_tokens = max_completion_tokens
        self.used = 0
        self.calls = 0

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        worst_case = estimate_prompt_tokens(prompt, system) + self._max_completion_tokens
        if self.used + worst_case > self._ceiling:
            raise DemoTaskTokenLimitError(
                f"demo task token limit reached ({self.used} used, next call up to {worst_case}, limit {self._ceiling})"
            )
        try:
            text = self._inner.generate(prompt, system=system)
        except Exception:
            # A failed call is charged only what the provider reported for it.
            self.used += self._reported() or 0
            raise
        self.calls += 1
        reported = self._reported()
        self.used += reported if reported is not None else worst_case
        return text

    def _reported(self) -> Optional[int]:
        usage = getattr(self._inner, "last_usage", None)
        return int(usage.total_tokens) if usage is not None else None
