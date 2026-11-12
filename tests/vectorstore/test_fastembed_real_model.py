"""The REAL bge-small model through fastembed (no fake). Skipped where
fastembed is not installed (it requires Python >= 3.10; CI and the image
run 3.11). Downloads the pinned revision (~67 MB) once, unless
EMBEDDING_MODEL_PATH points at a pre-downloaded copy."""

import math
import os

import pytest

import config

fastembed = pytest.importorskip("fastembed", reason="fastembed not installed (requires Python >= 3.10)")

from vectorstore.embeddings import fastembed_provider as module  # noqa: E402
from vectorstore.embeddings.fastembed_provider import FastEmbedEmbeddingProvider  # noqa: E402

MODEL = config.FASTEMBED_MODELS[config.DEFAULT_FASTEMBED_MODEL]


@pytest.fixture(scope="module")
def provider():
    return FastEmbedEmbeddingProvider(
        model_name=MODEL.name, dimension=MODEL.dimension, hf_repo=MODEL.hf_repo, hf_revision=MODEL.hf_revision,
        batch_size=8, model_path=os.environ.get("REAL_EMBEDDING_MODEL_PATH") or None, threads=2,
    )


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_the_pinned_revision_is_what_gets_downloaded():
    path = module.download_pinned_model(MODEL.hf_repo, MODEL.hf_revision)
    assert MODEL.hf_revision in path
    assert os.path.isfile(os.path.join(path, "model_optimized.onnx"))


def test_real_vectors_have_the_declared_dimension_and_are_deterministic(provider):
    first = provider.embed(["def add_numbers(a, b):\n    return a + b"])
    again = provider.embed(["def add_numbers(a, b):\n    return a + b"])
    assert len(first[0]) == MODEL.dimension == 384
    assert _cosine(first[0], again[0]) > 0.9999


def test_embeddings_are_semantic_unlike_the_hash_stand_in(provider):
    query, related, unrelated = provider.embed(
        [
            "function that adds two numbers",
            "def add_numbers(a, b):\n    return a + b",
            "class DatabaseConnectionPool:\n    def acquire(self):\n        return self._pool.get()",
        ]
    )
    assert _cosine(query, related) > _cosine(query, unrelated) + 0.05
