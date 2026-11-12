"""BudgetedLLMProvider: the per-task token ceiling, unit level."""

import pytest

from demo.task_budget import BudgetedLLMProvider, DemoTaskTokenLimitError, estimate_prompt_tokens
from rag.llm.base import LLMProvider
from rag.llm.openai_compatible_provider import LLMUsage


class Reporting(LLMProvider):
    def __init__(self, per_call=None, fail=False):
        self.per_call, self.fail, self.calls, self.last_usage = per_call, fail, 0, None

    def generate(self, prompt, system=None):
        self.calls += 1
        self.last_usage = None if self.per_call is None else LLMUsage(self.per_call, 0, self.per_call)
        if self.fail:
            raise RuntimeError("boom")
        return "ok"


def test_reported_usage_accumulates_across_calls():
    budget = BudgetedLLMProvider(Reporting(per_call=1_000), ceiling=10_000, max_completion_tokens=500)
    budget.generate("a" * 30)
    budget.generate("b" * 30)
    assert (budget.used, budget.calls) == (2_000, 2)


def test_the_ceiling_stops_a_later_call_before_it_is_made_midway():
    inner = Reporting(per_call=3_000)
    budget = BudgetedLLMProvider(inner, ceiling=5_000, max_completion_tokens=1_000)
    budget.generate("x" * 300)  # 0 + ~116 + 1000 <= 5000: allowed, reports 3000
    with pytest.raises(DemoTaskTokenLimitError, match="3000 used"):
        budget.generate("y" * 3_000)  # 3000 + ~1016 + 1000 > 5000: refused
    assert inner.calls == 1 and budget.used == 3_000


def test_without_reported_usage_the_worst_case_is_charged():
    budget = BudgetedLLMProvider(Reporting(per_call=None), ceiling=10_000, max_completion_tokens=500)
    budget.generate("z" * 90, system="s" * 30)
    assert budget.used == estimate_prompt_tokens("z" * 90, "s" * 30) + 500


def test_a_failed_call_is_charged_only_what_was_reported():
    budget = BudgetedLLMProvider(Reporting(per_call=250, fail=True), ceiling=10_000, max_completion_tokens=500)
    with pytest.raises(RuntimeError):
        budget.generate("q")
    assert budget.used == 250


def test_the_prompt_estimate_errs_high():
    # ~3 characters per token, plus per-message overhead: never below len/4.
    text = "def add_numbers(a, b):\n    return a + b\n" * 20
    assert estimate_prompt_tokens(text, None) >= len(text) / 4
