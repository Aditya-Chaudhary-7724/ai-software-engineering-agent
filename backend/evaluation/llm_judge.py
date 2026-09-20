"""Optional LLM-as-judge scoring for RAG answer quality.

Isolated behind its own small interface (`LLMJudge`), the same pattern
as every other pluggable provider in this project (`EmbeddingProvider`,
`LLMProvider`, `TestRunner`) — and, like those, completely OPTIONAL: no
evaluation runner in this package requires one to run, and none is
used unless a caller explicitly constructs one and passes it in (the
`--llm-judge` flag on `run_evaluation.py`, which then requires a real
`LLM_API_KEY`). This is never run by default, and its score is always
reported as an LLM's own judgment — informational, never mixed into
the deterministic pass/fail metrics computed elsewhere in this package.
"""

from abc import ABC, abstractmethod

from rag.llm.anthropic_provider import AnthropicLLMProvider

_JUDGE_PROMPT_TEMPLATE = (
    "You are evaluating whether an AI-generated answer is faithfully grounded in the "
    "provided code context and nothing else. Respond with ONLY a single integer from 0 "
    "to 10 (10 = fully grounded and relevant to the question, 0 = unrelated to or "
    "contradicts the context). No explanation, just the number.\n\n"
    "Question: {question}\n\nContext:\n{context}\n\nAnswer: {answer}\n\nScore (0-10):"
)


class LLMJudge(ABC):
    @abstractmethod
    def score_answer(self, question: str, context: str, answer: str) -> float:
        """Return a 0.0-1.0 judgment of whether `answer` is faithfully
        grounded in `context` for `question`. REQUIRES A REAL LLM — this
        is a model's own judgment, not a deterministic measurement, and
        must never be presented as one.
        """


class AnthropicLLMJudge(LLMJudge):
    """REQUIRES A REAL `LLM_API_KEY` — wraps `AnthropicLLMProvider` to
    produce a real model judgment. Never constructed by default.
    """

    def __init__(self, provider: AnthropicLLMProvider) -> None:
        self._provider = provider

    def score_answer(self, question: str, context: str, answer: str) -> float:
        prompt = _JUDGE_PROMPT_TEMPLATE.format(question=question, context=context, answer=answer)
        response = self._provider.generate(prompt).strip()
        try:
            score = float(response.split()[0])
        except (ValueError, IndexError):
            raise ValueError(f"LLM judge returned a non-numeric response: {response!r}")
        return max(0.0, min(10.0, score)) / 10.0
