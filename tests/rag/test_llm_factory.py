"""LLM provider selection (rag/llm/factory.py + backend/config.py).

The default (LLM_PROVIDER unset) must keep the original behaviour, and an
invalid configuration must be refused, never silently replaced by the stub.
"""

import subprocess
import sys
from pathlib import Path

import pytest

import config
from rag.llm.anthropic_provider import AnthropicLLMProvider
from rag.llm.exceptions import LLMConfigurationError
from rag.llm.factory import configured_llm_provider
from rag.llm.openai_compatible_provider import OpenAICompatibleLLMProvider
from rag.llm.stub_provider import StubLLMProvider

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
FAKE_KEY = "gsk_fake_key_that_must_never_be_printed_123"


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)


def test_unset_without_a_key_is_the_stub():
    provider, name = configured_llm_provider()
    assert isinstance(provider, StubLLMProvider) and name == "stub"


def test_unset_with_an_anthropic_key_is_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-real")
    provider, name = configured_llm_provider()
    assert isinstance(provider, AnthropicLLMProvider) and name == "anthropic"


def test_groq_is_built_from_configuration(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
    monkeypatch.setenv("LLM_MODEL", " vendor/model-id ")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "low")
    monkeypatch.setenv("LLM_MAX_COMPLETION_TOKENS", "3000")

    provider, name = configured_llm_provider()

    assert name == "groq" and isinstance(provider, OpenAICompatibleLLMProvider)
    assert provider.model == "vendor/model-id"
    assert str(provider._client.base_url).rstrip("/") == config.GROQ_BASE_URL
    assert provider._max_completion_tokens == 3000 and provider._reasoning_effort == "low"


def test_explicit_stub_wins_even_with_a_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-real")
    assert configured_llm_provider()[1] == "stub"


@pytest.mark.parametrize(
    "env, expected",
    [
        ({"LLM_PROVIDER": "groq"}, ["requires GROQ_API_KEY", "requires LLM_MODEL"]),
        ({"LLM_PROVIDER": "groq", "GROQ_API_KEY": FAKE_KEY}, ["requires LLM_MODEL"]),
        ({"LLM_PROVIDER": "groq", "GROQ_API_KEY": FAKE_KEY, "LLM_MODEL": "m", "LLM_MAX_COMPLETION_TOKENS": "0"}, ["LLM_MAX_COMPLETION_TOKENS"]),
        ({"LLM_PROVIDER": "groq", "GROQ_API_KEY": FAKE_KEY, "LLM_MODEL": "m", "LLM_MAX_COMPLETION_TOKENS": "lots"}, ["LLM_MAX_COMPLETION_TOKENS"]),
        ({"LLM_PROVIDER": "anthropic"}, ["requires LLM_API_KEY"]),
        ({"LLM_PROVIDER": "gpt5-please"}, ["must be one of"]),
    ],
)
def test_invalid_configuration_is_refused_not_stubbed(monkeypatch, env, expected):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(LLMConfigurationError) as caught:
        configured_llm_provider()
    message = str(caught.value)
    for fragment in expected:
        assert fragment in message
    assert FAKE_KEY not in message


def _check_script(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BACKEND_DIR / "scripts" / "check_production_config.py")],
        cwd=BACKEND_DIR,
        env={"PATH": "/usr/bin:/bin", "DATABASE_URL": "postgresql://user@localhost/db", **env},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_check_production_config_fails_on_an_incomplete_groq_setup():
    result = _check_script({"LLM_PROVIDER": "groq", "GROQ_API_KEY": FAKE_KEY})
    assert result.returncode == 1
    assert "LLM provider: groq" in result.stdout
    assert "requires LLM_MODEL" in result.stdout
    assert FAKE_KEY not in result.stdout + result.stderr


def test_check_production_config_passes_a_complete_groq_setup():
    result = _check_script({"LLM_PROVIDER": "groq", "GROQ_API_KEY": FAKE_KEY, "LLM_MODEL": "vendor/model"})
    assert result.returncode == 0, result.stdout
    assert "LLM provider: groq" in result.stdout
    assert "GROQ_API_KEY" in result.stdout and FAKE_KEY not in result.stdout
