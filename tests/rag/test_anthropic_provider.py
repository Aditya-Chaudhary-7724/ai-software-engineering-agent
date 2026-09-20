"""Unit tests for AnthropicLLMProvider.

No real network calls are made: the Anthropic client is monkeypatched
to verify request/response handling. Whether a *real* Anthropic
account and key actually returns a completion is REQUIRES EXTERNAL
SERVICE and is not something this test suite claims to verify.
"""

from types import SimpleNamespace

import pytest

from rag.llm import anthropic_provider
from vectorstore.exceptions import MissingCredentialsError


def test_missing_api_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(MissingCredentialsError):
        anthropic_provider.AnthropicLLMProvider(api_key=None)


class _FakeMessages:
    def __init__(self):
        self.last_call = None

    def create(self, **kwargs):
        self.last_call = kwargs
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="42")])


class _FakeAnthropicClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.messages = _FakeMessages()


def test_generate_calls_client_and_returns_text(monkeypatch):
    monkeypatch.setattr(anthropic_provider, "Anthropic", _FakeAnthropicClient)

    provider = anthropic_provider.AnthropicLLMProvider(api_key="test-key")
    result = provider.generate("What is the answer?", system="Be terse.")

    assert result == "42"
    assert provider._client.messages.last_call["model"] == anthropic_provider.DEFAULT_MODEL
    assert provider._client.messages.last_call["system"] == "Be terse."
    assert provider._client.messages.last_call["messages"] == [
        {"role": "user", "content": "What is the answer?"}
    ]


def test_generate_without_system_sends_empty_string(monkeypatch):
    monkeypatch.setattr(anthropic_provider, "Anthropic", _FakeAnthropicClient)

    provider = anthropic_provider.AnthropicLLMProvider(api_key="test-key")
    provider.generate("hello")

    assert provider._client.messages.last_call["system"] == ""


def test_uses_env_var_when_no_explicit_key(monkeypatch):
    monkeypatch.setattr(anthropic_provider, "Anthropic", _FakeAnthropicClient)
    monkeypatch.setenv("LLM_API_KEY", "env-key")

    provider = anthropic_provider.AnthropicLLMProvider()

    assert provider._client.api_key == "env-key"
