"""OpenAICompatibleLLMProvider (used for Groq). No network: the `OpenAI`
client is replaced with a fake that records the request."""

from types import SimpleNamespace

import httpx
import openai
import pytest

from rag.llm import openai_compatible_provider as module
from rag.llm.exceptions import LLMEmptyResponseError, LLMQuotaExceededError


class _FakeCompletions:
    def __init__(self, result):
        self.result = result
        self.last_call = None

    def create(self, **kwargs):
        self.last_call = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _FakeClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.chat = SimpleNamespace(completions=_FakeCompletions(_FakeClient.next_result))
        _FakeClient.instances.append(self)


def _completion(content, finish_reason="stop", reasoning=None):
    message = SimpleNamespace(content=content, reasoning=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


def _provider(monkeypatch, result, **overrides):
    _FakeClient.next_result = result
    monkeypatch.setattr(module, "OpenAI", _FakeClient)
    kwargs = dict(api_key="gsk_test_not_real", base_url="https://llm.example/v1", model="some-model", max_completion_tokens=512)
    kwargs.update(overrides)
    return module.OpenAICompatibleLLMProvider(**kwargs)


def _rate_limit_error(headers):
    request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
    response = httpx.Response(429, headers=headers, request=request)
    return openai.RateLimitError("rate limited", response=response, body=None)


def test_sends_system_and_user_messages_to_the_configured_endpoint(monkeypatch):
    provider = _provider(monkeypatch, _completion("add_numbers returns a + b."))

    assert provider.generate("What does add_numbers do?", system="Be terse.") == "add_numbers returns a + b."
    client = _FakeClient.instances[-1]
    assert client.kwargs["base_url"] == "https://llm.example/v1"
    assert client.kwargs["api_key"] == "gsk_test_not_real"
    call = client.chat.completions.last_call
    assert call["model"] == "some-model"
    assert call["max_completion_tokens"] == 512
    assert call["messages"] == [
        {"role": "system", "content": "Be terse."},
        {"role": "user", "content": "What does add_numbers do?"},
    ]
    assert "reasoning_effort" not in call  # only sent when configured


def test_hidden_sdk_retries_are_disabled(monkeypatch):
    # The SDK retries 429s by default; on a free tier that silently spends quota.
    _provider(monkeypatch, _completion("ok"))
    assert _FakeClient.instances[-1].kwargs["max_retries"] == 0


def test_no_system_message_when_none_is_given(monkeypatch):
    provider = _provider(monkeypatch, _completion("ok"))
    provider.generate("hello")
    assert _FakeClient.instances[-1].chat.completions.last_call["messages"] == [{"role": "user", "content": "hello"}]


def test_reasoning_effort_is_passed_through_only_when_configured(monkeypatch):
    provider = _provider(monkeypatch, _completion("ok"), reasoning_effort="low")
    provider.generate("hello")
    assert _FakeClient.instances[-1].chat.completions.last_call["reasoning_effort"] == "low"


def test_only_the_answer_is_returned_never_the_reasoning(monkeypatch):
    provider = _provider(monkeypatch, _completion("The answer.", reasoning="secret chain of thought"))
    assert provider.generate("q") == "The answer."


def test_an_inline_think_block_is_stripped(monkeypatch):
    provider = _provider(monkeypatch, _completion("<think>step 1, step 2</think>\nThe answer."))
    result = provider.generate("q")
    assert result == "The answer."
    assert "step 1" not in result


@pytest.mark.parametrize("content", [None, "", "   ", "<think>only reasoning</think>"])
def test_an_empty_answer_is_an_error_not_an_empty_string(monkeypatch, content):
    provider = _provider(monkeypatch, _completion(content, finish_reason="length"))
    with pytest.raises(LLMEmptyResponseError, match="finish_reason=length"):
        provider.generate("q")


def test_a_rate_limit_becomes_a_quota_error_with_retry_after(monkeypatch):
    provider = _provider(monkeypatch, _rate_limit_error({"retry-after": "17"}))
    with pytest.raises(LLMQuotaExceededError) as caught:
        provider.generate("q")
    assert caught.value.retry_after_seconds == 17


@pytest.mark.parametrize("headers", [{}, {"retry-after": "soon"}])
def test_a_missing_or_unparseable_retry_after_is_none(monkeypatch, headers):
    provider = _provider(monkeypatch, _rate_limit_error(headers))
    with pytest.raises(LLMQuotaExceededError) as caught:
        provider.generate("q")
    assert caught.value.retry_after_seconds is None


def test_other_provider_errors_are_not_disguised_as_quota_errors(monkeypatch):
    request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
    error = openai.InternalServerError("boom", response=httpx.Response(500, request=request), body=None)
    provider = _provider(monkeypatch, error)
    with pytest.raises(openai.InternalServerError):
        provider.generate("q")


def _with_usage(completion, prompt_tokens, completion_tokens, reasoning_tokens=None):
    details = None if reasoning_tokens is None else SimpleNamespace(reasoning_tokens=reasoning_tokens)
    completion.usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        completion_tokens_details=details,
    )
    return completion


def test_reported_token_usage_is_recorded(monkeypatch):
    provider = _provider(monkeypatch, _with_usage(_completion("ok"), 120, 30, reasoning_tokens=12))
    assert provider.last_usage is None
    provider.generate("q")
    assert provider.last_usage == module.LLMUsage(prompt_tokens=120, completion_tokens=30, total_tokens=150, reasoning_tokens=12)


def test_usage_without_a_reasoning_breakdown(monkeypatch):
    provider = _provider(monkeypatch, _with_usage(_completion("ok"), 10, 5))
    provider.generate("q")
    assert provider.last_usage.total_tokens == 15 and provider.last_usage.reasoning_tokens is None


def test_no_usage_reported_is_none_not_zero(monkeypatch):
    provider = _provider(monkeypatch, _completion("ok"))  # no usage attribute
    provider.generate("q")
    assert provider.last_usage is None


def test_usage_is_cleared_when_a_call_fails(monkeypatch):
    provider = _provider(monkeypatch, _with_usage(_completion("ok"), 10, 5))
    provider.generate("q")
    provider._client.chat.completions.result = _rate_limit_error({})
    with pytest.raises(LLMQuotaExceededError):
        provider.generate("q")
    assert provider.last_usage is None
