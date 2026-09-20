from rag.llm.stub_provider import StubLLMProvider
from rag.prompt import build_prompt


def test_stub_provider_reports_context_length():
    provider = StubLLMProvider()
    prompt = build_prompt("What does add() do?", "def add(a, b): return a + b")

    response = provider.generate(prompt)

    assert "stub-llm" in response
    assert str(len(prompt)) in response


def test_stub_provider_handles_no_context_case():
    provider = StubLLMProvider()
    prompt = build_prompt("What does add() do?", context="")

    response = provider.generate(prompt)

    assert "No relevant code context was found" in response
