"""Embedding provider selection (vectorstore/embeddings/factory.py +
backend/config.py): unset keeps the original rule, fastembed is built from
an allowlisted model, and invalid settings are refused, never stubbed."""

import subprocess
import sys
from pathlib import Path

import pytest

import config
from vectorstore.embeddings.factory import configured_embedding_provider
from vectorstore.embeddings.fastembed_provider import FastEmbedEmbeddingProvider
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.embeddings.openai_provider import OpenAIEmbeddingProvider
from vectorstore.exceptions import EmbeddingConfigurationError

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)


def test_unset_without_a_key_is_the_hash_stand_in():
    provider, name = configured_embedding_provider()
    assert isinstance(provider, DeterministicLocalEmbeddingProvider) and name == "deterministic_local"


def test_unset_with_an_openai_key_is_openai(monkeypatch):
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-key-not-real")
    provider, name = configured_embedding_provider()
    assert isinstance(provider, OpenAIEmbeddingProvider) and name == "openai"


def test_fastembed_is_built_from_the_allowlist_without_loading_a_model(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fastembed")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "4")
    monkeypatch.setenv("EMBEDDING_THREADS", "2")
    monkeypatch.setenv("EMBEDDING_MODEL_PATH", str(tmp_path))

    provider, name = configured_embedding_provider()

    model = config.FASTEMBED_MODELS["BAAI/bge-small-en-v1.5"]
    assert isinstance(provider, FastEmbedEmbeddingProvider) and name == "fastembed:BAAI/bge-small-en-v1.5"
    assert (provider.dimension, provider._hf_revision) == (384, model.hf_revision)
    assert (provider._batch_size, provider._threads, provider._model_path) == (4, 2, str(tmp_path))


def test_the_default_batch_size_is_the_measured_low_memory_one(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fastembed")
    assert configured_embedding_provider()[0]._batch_size == 8


@pytest.mark.parametrize(
    "env, expected",
    [
        ({"EMBEDDING_PROVIDER": "openai"}, "requires EMBEDDING_API_KEY"),
        ({"EMBEDDING_PROVIDER": "fastembed", "EMBEDDING_MODEL": "some/other-model"}, "EMBEDDING_MODEL must be one of"),
        ({"EMBEDDING_PROVIDER": "fastembed", "EMBEDDING_BATCH_SIZE": "0"}, "EMBEDDING_BATCH_SIZE"),
        ({"EMBEDDING_PROVIDER": "fastembed", "EMBEDDING_BATCH_SIZE": "1000"}, "EMBEDDING_BATCH_SIZE"),
        ({"EMBEDDING_PROVIDER": "fastembed", "EMBEDDING_THREADS": "many"}, "EMBEDDING_THREADS"),
        ({"EMBEDDING_PROVIDER": "fastembed", "EMBEDDING_MODEL_PATH": "relative/dir"}, "absolute path"),
        ({"EMBEDDING_PROVIDER": "word2vec"}, "must be one of"),
    ],
)
def test_invalid_configuration_is_refused(monkeypatch, env, expected):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(EmbeddingConfigurationError, match=expected):
        configured_embedding_provider()


def test_every_allowlisted_model_has_a_pinned_full_commit_revision():
    for model in config.FASTEMBED_MODELS.values():
        assert len(model.hf_revision) == 40 and all(c in "0123456789abcdef" for c in model.hf_revision)


def _check_script(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BACKEND_DIR / "scripts" / "check_production_config.py")],
        cwd=BACKEND_DIR,
        env={"PATH": "/usr/bin:/bin", "DATABASE_URL": "postgresql://user@localhost/db", **env},
        capture_output=True, text=True, timeout=30,
    )


def test_check_production_config_reports_the_embedding_provider_and_its_problems():
    ok = _check_script({"EMBEDDING_PROVIDER": "fastembed"})
    assert ok.returncode == 0 and "Embedding provider: fastembed" in ok.stdout
    bad = _check_script({"EMBEDDING_PROVIDER": "fastembed", "EMBEDDING_MODEL": "some/other-model"})
    assert bad.returncode == 1 and "EMBEDDING_MODEL must be one of" in bad.stdout
