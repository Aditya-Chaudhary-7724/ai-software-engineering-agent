"""The single place that turns configuration into an embedding provider.

Returns `(provider, name)`. The name is recorded with every index (jobs
today; per repository with the 384-dimension schema change) so retrieval can
refuse to mix vectors from different models: "openai",
"deterministic_local", or "fastembed:<model>" (the model is part of the
name because two local models are not interchangeable).
"""

import os
from typing import Tuple

import config
from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.embeddings.local_provider import DeterministicLocalEmbeddingProvider
from vectorstore.exceptions import EmbeddingConfigurationError


def configured_embedding_provider() -> Tuple[EmbeddingProvider, str]:
    problems = config.embedding_configuration_problems()
    if problems:
        raise EmbeddingConfigurationError("; ".join(problems))
    name = config.embedding_provider_name()
    if name == "openai":
        from vectorstore.embeddings.openai_provider import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(), "openai"
    if name == "fastembed":
        from vectorstore.embeddings.fastembed_provider import FastEmbedEmbeddingProvider

        model = config.FASTEMBED_MODELS[config.fastembed_model()]
        threads = os.environ.get("EMBEDDING_THREADS", "").strip()
        provider = FastEmbedEmbeddingProvider(
            model_name=model.name,
            dimension=model.dimension,
            hf_repo=model.hf_repo,
            hf_revision=model.hf_revision,
            batch_size=config.embedding_batch_size(),
            model_path=os.environ.get("EMBEDDING_MODEL_PATH", "").strip() or None,
            threads=int(threads) if threads else None,
        )
        return provider, f"fastembed:{model.name}"
    return DeterministicLocalEmbeddingProvider(), "deterministic_local"
