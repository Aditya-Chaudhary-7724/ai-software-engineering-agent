"""Unit tests for AnthropicLLMJudge's response parsing, with a fake
provider (duck-typed — Python doesn't enforce the type hint at
runtime) standing in for AnthropicLLMProvider. No real LLM_API_KEY or
network call involved; this only verifies the judge correctly parses a
model's response into a 0.0-1.0 score, not that a live model produces
a meaningful score.
"""

import pytest

from evaluation.llm_judge import AnthropicLLMJudge


class _FakeProvider:
    def __init__(self, response: str) -> None:
        self._response = response

    def generate(self, prompt, system=None):
        return self._response


def test_parses_a_numeric_score_into_zero_to_one_range():
    judge = AnthropicLLMJudge(_FakeProvider("8"))

    score = judge.score_answer("question", "context", "answer")

    assert score == pytest.approx(0.8)


def test_clamps_out_of_range_scores():
    assert AnthropicLLMJudge(_FakeProvider("15")).score_answer("q", "c", "a") == 1.0
    assert AnthropicLLMJudge(_FakeProvider("-3")).score_answer("q", "c", "a") == 0.0


def test_raises_on_a_non_numeric_response():
    judge = AnthropicLLMJudge(_FakeProvider("I think it's pretty good"))

    with pytest.raises(ValueError):
        judge.score_answer("q", "c", "a")
