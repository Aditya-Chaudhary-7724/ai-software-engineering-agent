"""backend/scripts/manual_groq_smoke.py, with a FAKE network client: never
calls Groq. Checks it makes exactly one call, prints what it promises, and
never prints the key."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from rag.llm import openai_compatible_provider

SCRIPT = Path(__file__).resolve().parents[2] / "backend" / "scripts" / "manual_groq_smoke.py"
FAKE_KEY = "gsk_fake_smoke_key_must_never_be_printed_42"


def _load_script():
    spec = importlib.util.spec_from_file_location("manual_groq_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _CountingClient:
    calls = []
    result = None

    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        _CountingClient.calls.append(kwargs)
        if isinstance(_CountingClient.result, Exception):
            raise _CountingClient.result
        return _CountingClient.result


@pytest.fixture
def fake_groq(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
    monkeypatch.setenv("LLM_MODEL", "vendor/model-id")
    monkeypatch.setattr(openai_compatible_provider, "OpenAI", _CountingClient)
    _CountingClient.calls = []
    message = SimpleNamespace(content="add_numbers returns a + b.")
    usage = SimpleNamespace(
        prompt_tokens=180, completion_tokens=40, total_tokens=220, completion_tokens_details=SimpleNamespace(reasoning_tokens=25)
    )
    _CountingClient.result = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage)
    return _CountingClient


def test_makes_exactly_one_call_and_reports_provider_model_latency_and_usage(fake_groq, capsys):
    assert _load_script().main(["--confirm-real-call"]) == 0

    out = capsys.readouterr().out
    assert len(fake_groq.calls) == 1
    assert fake_groq.calls[0]["messages"][0]["role"] == "system"  # the real Ask system prompt
    assert "UNTRUSTED DATA" in fake_groq.calls[0]["messages"][0]["content"]
    for expected in ("provider: groq", "model: vendor/model-id", "latency: ", "prompt=180 completion=40 (reasoning=25) total=220"):
        assert expected in out
    assert FAKE_KEY not in out


def test_refuses_without_the_confirmation_flag(fake_groq, capsys):
    assert _load_script().main([]) == 2
    assert fake_groq.calls == []


def test_refuses_when_the_provider_is_not_groq(fake_groq, monkeypatch, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    assert _load_script().main(["--confirm-real-call"]) == 2
    assert fake_groq.calls == [] and "not 'groq'" in capsys.readouterr().out


def test_refuses_an_incomplete_configuration_without_calling(fake_groq, monkeypatch, capsys):
    monkeypatch.delenv("LLM_MODEL")
    assert _load_script().main(["--confirm-real-call"]) == 2
    out = capsys.readouterr().out
    assert fake_groq.calls == [] and "requires LLM_MODEL" in out and FAKE_KEY not in out


def test_a_rate_limit_is_reported_with_exit_3_after_one_call(fake_groq, capsys):
    request = httpx.Request("POST", "https://api.groq.example/v1/chat/completions")
    fake_groq.result = openai.RateLimitError(
        "rate limited", response=httpx.Response(429, headers={"retry-after": "30"}, request=request), body=None
    )
    assert _load_script().main(["--confirm-real-call"]) == 3
    out = capsys.readouterr().out
    assert len(fake_groq.calls) == 1 and "retry after: 30" in out and FAKE_KEY not in out


def test_other_failures_are_redacted_and_exit_4(fake_groq, capsys):
    fake_groq.result = RuntimeError(f"upstream said: bad key {FAKE_KEY}")
    assert _load_script().main(["--confirm-real-call"]) == 4
    out = capsys.readouterr().out
    assert len(fake_groq.calls) == 1 and "CALL FAILED: RuntimeError" in out and FAKE_KEY not in out
