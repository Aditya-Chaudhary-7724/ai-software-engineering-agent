from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.models import EMBEDDING_DIMENSION


def test_local_provider_produces_correct_dimension():
    provider = DeterministicLocalEmbeddingProvider()
    vectors = provider.embed(["hello world"])

    assert len(vectors) == 1
    assert len(vectors[0]) == EMBEDDING_DIMENSION


def test_local_provider_is_deterministic():
    provider = DeterministicLocalEmbeddingProvider()

    first = provider.embed(["def add(a, b): return a + b"])[0]
    second = provider.embed(["def add(a, b): return a + b"])[0]

    assert first == second


def test_local_provider_differs_for_different_text():
    provider = DeterministicLocalEmbeddingProvider()

    a = provider.embed(["function one"])[0]
    b = provider.embed(["function two"])[0]

    assert a != b


def test_local_provider_handles_multiple_texts_in_order():
    provider = DeterministicLocalEmbeddingProvider()

    vectors = provider.embed(["alpha", "beta", "gamma"])

    assert len(vectors) == 3
    assert vectors[0] == provider.embed(["alpha"])[0]
    assert vectors[2] == provider.embed(["gamma"])[0]
