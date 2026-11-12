"""FastEmbedEmbeddingProvider with a FAKE `fastembed` module, so these run
on any interpreter and never download a model. The real model is exercised
by test_fastembed_real_model.py where fastembed is installed."""

import sys
import types

import numpy as np
import pytest

import config
from vectorstore.embeddings import fastembed_provider as module
from vectorstore.embeddings.fastembed_provider import FastEmbedEmbeddingProvider
from vectorstore.exceptions import EmbeddingConfigurationError, EmbeddingDimensionError

MODEL = config.FASTEMBED_MODELS[config.DEFAULT_FASTEMBED_MODEL]


class _FakeTextEmbedding:
    loads = []

    def __init__(self, model_name, specific_model_path=None, threads=None, **kwargs):
        _FakeTextEmbedding.loads.append({"model_name": model_name, "path": specific_model_path, "threads": threads})
        self.batch_sizes = []
        self.dimension = _FakeTextEmbedding.dimension

    def embed(self, texts, batch_size=256):
        self.batch_sizes.append(batch_size)
        for text in texts:
            yield np.full(self.dimension, float(len(text)), dtype=np.float32)


@pytest.fixture(autouse=True)
def fake_fastembed(monkeypatch):
    _FakeTextEmbedding.loads = []
    _FakeTextEmbedding.dimension = MODEL.dimension
    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=_FakeTextEmbedding))
    monkeypatch.setattr(module, "_MODEL_CACHE", {})
    downloads = []

    def fake_download(repo, revision, target_dir=None):
        downloads.append((repo, revision))
        return f"/fake/hf-cache/{revision}"

    monkeypatch.setattr(module, "download_pinned_model", fake_download)
    return downloads


def _provider(**overrides):
    kwargs = dict(
        model_name=MODEL.name, dimension=MODEL.dimension, hf_repo=MODEL.hf_repo,
        hf_revision=MODEL.hf_revision, batch_size=8,
    )
    kwargs.update(overrides)
    return FastEmbedEmbeddingProvider(**kwargs)


def test_embeds_in_order_with_the_configured_batch_size():
    provider = _provider(batch_size=4)
    vectors = provider.embed(["a", "bbb"])
    assert [v[0] for v in vectors] == [1.0, 3.0] and all(len(v) == 384 for v in vectors)
    assert all(isinstance(x, float) for x in vectors[0])
    assert provider._model().batch_sizes == [4]


def test_without_a_model_path_it_downloads_exactly_the_pinned_revision(fake_fastembed):
    _provider().embed(["x"])
    assert fake_fastembed == [(MODEL.hf_repo, MODEL.hf_revision)]
    assert _FakeTextEmbedding.loads[0]["path"] == f"/fake/hf-cache/{MODEL.hf_revision}"


def test_a_baked_model_path_is_used_and_nothing_is_downloaded(fake_fastembed, tmp_path):
    _provider(model_path=str(tmp_path), threads=2).embed(["x"])
    assert fake_fastembed == []
    assert _FakeTextEmbedding.loads == [{"model_name": MODEL.name, "path": str(tmp_path), "threads": 2}]


def test_a_missing_model_path_is_a_configuration_error(tmp_path):
    with pytest.raises(EmbeddingConfigurationError, match="EMBEDDING_MODEL_PATH"):
        _provider(model_path=str(tmp_path / "absent")).embed(["x"])


def test_the_model_is_loaded_once_per_process_not_per_provider():
    _provider().embed(["x"])
    _provider().embed(["y"])
    assert len(_FakeTextEmbedding.loads) == 1


def test_no_model_is_loaded_for_an_empty_batch():
    assert _provider().embed([]) == []
    assert _FakeTextEmbedding.loads == []


def test_a_wrong_dimension_is_refused_never_returned():
    _FakeTextEmbedding.dimension = 768
    with pytest.raises(EmbeddingDimensionError, match="768 dimensions, expected 384"):
        _provider().embed(["x"])


def test_fastembed_not_installed_is_a_configuration_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "fastembed", None)  # makes `import fastembed` raise ImportError
    with pytest.raises(EmbeddingConfigurationError, match="not installed"):
        _provider().embed(["x"])
