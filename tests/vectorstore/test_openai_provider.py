"""Unit tests for OpenAIEmbeddingProvider.

No real network calls are made: the OpenAI client is monkeypatched to
verify request/response handling. Whether a *real* OpenAI account and
key actually returns embeddings is REQUIRES EXTERNAL SERVICE and is
not something this test suite claims to verify.
"""

from types import SimpleNamespace

import pytest

from vectorstore.embeddings import openai_provider
from vectorstore.exceptions import MissingCredentialsError


def test_missing_api_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)

    with pytest.raises(MissingCredentialsError):
        openai_provider.OpenAIEmbeddingProvider(api_key=None)


class _FakeEmbeddings:
    def __init__(self):
        self.last_call = None

    def create(self, model, input):
        self.last_call = {"model": model, "input": input}
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3]) for _ in input])


class _FakeOpenAIClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.embeddings = _FakeEmbeddings()


def test_embed_calls_client_and_returns_vectors(monkeypatch):
    monkeypatch.setattr(openai_provider, "OpenAI", _FakeOpenAIClient)

    provider = openai_provider.OpenAIEmbeddingProvider(api_key="test-key")
    result = provider.embed(["hello", "world"])

    assert result == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert provider._client.embeddings.last_call == {
        "model": openai_provider.DEFAULT_MODEL,
        "input": ["hello", "world"],
    }


def test_uses_env_var_when_no_explicit_key(monkeypatch):
    monkeypatch.setattr(openai_provider, "OpenAI", _FakeOpenAIClient)
    monkeypatch.setenv("EMBEDDING_API_KEY", "env-key")

    provider = openai_provider.OpenAIEmbeddingProvider()

    assert provider._client.api_key == "env-key"
