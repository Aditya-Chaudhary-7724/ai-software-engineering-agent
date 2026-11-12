"""Demo Ask through the API with the real limiter and PostgreSQL. No real
LLM: the Groq provider's generate() is patched (and sets the usage a real
call would report)."""

import psycopg
import pytest

from api.v1 import API_V1_PREFIX
from rag.llm.exceptions import LLMQuotaExceededError
from rag.llm.openai_compatible_provider import LLMUsage, OpenAICompatibleLLMProvider
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.service import IndexingService

from tests.api.conftest import auth_headers, requires_postgres
from tests.api.test_demo_scope import DEMO_TOKEN, demo_headers
from tests.vectorstore.conftest import TEST_DATABASE_URL, vector_store  # noqa: F401

pytestmark = requires_postgres
CLIENT = {"X-Demo-Client-IP": "203.0.113.7"}


def _sql(sql):
    with psycopg.connect(TEST_DATABASE_URL) as conn:
        cursor = conn.execute(sql)
        return cursor.fetchall() if cursor.description else None


@pytest.fixture
def demo_repo(monkeypatch, vector_store, tmp_path):  # noqa: F811
    root = tmp_path / "demo"
    root.mkdir()
    (root / "calc.py").write_text("def add_numbers(a, b):\n    return a + b\n")
    repository_id = IndexingService(DeterministicLocalEmbeddingProvider(), vector_store).index_repository(str(root)).repository_id
    env = {
        "DATABASE_URL": TEST_DATABASE_URL, "LLM_PROVIDER": "groq", "GROQ_API_KEY": "gsk_" + "f" * 40,
        "LLM_MODEL": "vendor/operator-model", "DEMO_LLM_MODEL": "vendor/demo-model",
        "DEMO_API_SERVICE_TOKEN": DEMO_TOKEN, "DEMO_REPOSITORY_IDS": str(repository_id),
        "DEMO_ASK_PER_IP_PER_HOUR": "100", "DEMO_ASK_GLOBAL_PER_MINUTE": "100",
        "DEMO_DAILY_TOKEN_BUDGET": "100000", "DEMO_ASK_TOKEN_RESERVE": "5000",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    _sql("TRUNCATE demo_rate_events, demo_token_usage")

    def generate(self, prompt, system=None):
        self.last_usage = LLMUsage(prompt_tokens=700, completion_tokens=77, total_tokens=777)
        return "add_numbers returns a + b."

    monkeypatch.setattr(OpenAICompatibleLLMProvider, "generate", generate)
    yield repository_id
    _sql("TRUNCATE demo_rate_events, demo_token_usage")


def _ask(client, repository_id, headers):
    return client.post(f"{API_V1_PREFIX}/repositories/{repository_id}/ask", headers=headers, json={"question": "add_numbers"})


def test_a_demo_ask_without_the_client_ip_is_refused_before_any_work(api_client, demo_repo):
    response = _ask(api_client, demo_repo, demo_headers())
    assert (response.status_code, response.json()) == (400, {"error": "demo_client_ip_required"})
    assert _sql("SELECT count(*) FROM demo_rate_events") == [(0,)]


def test_a_successful_demo_ask_is_charged_its_reported_usage(api_client, demo_repo):
    assert _ask(api_client, demo_repo, {**demo_headers(), **CLIENT}).status_code == 200
    assert _sql("SELECT tokens, requests FROM demo_token_usage") == [(777, 1)]


def test_the_per_ip_limit_answers_429_with_retry_after(api_client, demo_repo, monkeypatch):
    monkeypatch.setenv("DEMO_ASK_PER_IP_PER_HOUR", "1")
    assert _ask(api_client, demo_repo, {**demo_headers(), **CLIENT}).status_code == 200
    refused = _ask(api_client, demo_repo, {**demo_headers(), **CLIENT})
    assert refused.status_code == 429 and refused.json()["error"] == "demo_rate_limited"
    assert int(refused.headers["retry-after"]) == refused.json()["retry_after_seconds"] > 0


def test_the_daily_budget_answers_429_until_midnight_utc(api_client, demo_repo, monkeypatch):
    monkeypatch.setenv("DEMO_DAILY_TOKEN_BUDGET", "5000")
    assert _ask(api_client, demo_repo, {**demo_headers(), "X-Demo-Client-IP": "203.0.113.1"}).status_code == 200
    _sql("UPDATE demo_token_usage SET tokens = 5000")  # as if usage had been reported at the full reserve
    refused = _ask(api_client, demo_repo, {**demo_headers(), "X-Demo-Client-IP": "203.0.113.2"})
    assert refused.status_code == 429 and refused.json()["error"] == "demo_daily_limit_reached"
    assert 0 < refused.json()["retry_after_seconds"] <= 86_401


def test_a_provider_quota_error_refunds_the_reservation(api_client, demo_repo, monkeypatch):
    def quota(self, prompt, system=None):
        raise LLMQuotaExceededError("429", retry_after_seconds=9)

    monkeypatch.setattr(OpenAICompatibleLLMProvider, "generate", quota)
    response = _ask(api_client, demo_repo, {**demo_headers(), **CLIENT})
    assert (response.status_code, response.json()["error"]) == (429, "llm_quota_exceeded")
    assert _sql("SELECT tokens, requests FROM demo_token_usage") == [(0, 0)]


def test_the_operator_is_never_limited_or_recorded(api_client, demo_repo, monkeypatch):
    monkeypatch.setenv("DEMO_ASK_PER_IP_PER_HOUR", "1")
    for _ in range(3):
        assert _ask(api_client, demo_repo, {**auth_headers(), **CLIENT}).status_code == 200
    assert _sql("SELECT count(*) FROM demo_rate_events") == [(0,)]
    assert _sql("SELECT count(*) FROM demo_token_usage") == [(0,)]
