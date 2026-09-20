"""Production embedding provider, backed by OpenAI's embeddings API.

REQUIRES EXTERNAL SERVICE / CREDENTIALS: reading EMBEDDING_API_KEY from
the environment and making a real network call to OpenAI. Never
hardcode the key; it must come from the environment (see .env.example).
"""

import os
from typing import Optional

from openai import OpenAI

from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.exceptions import MissingCredentialsError
from vectorstore.models import EMBEDDING_DIMENSION

DEFAULT_MODEL = "text-embedding-3-small"  # 1536 dimensions, matches EMBEDDING_DIMENSION


class OpenAIEmbeddingProvider(EmbeddingProvider):
    dimension = EMBEDDING_DIMENSION

    def __init__(self, api_key: Optional[str] = None, model: str = DEFAULT_MODEL) -> None:
        resolved_key = api_key or os.environ.get("EMBEDDING_API_KEY")
        if not resolved_key:
            raise MissingCredentialsError(
                "EMBEDDING_API_KEY is not set. Add a real OpenAI API key to your local "
                ".env to use OpenAIEmbeddingProvider; it is never read from source code."
            )
        self._client = OpenAI(api_key=resolved_key)
        self._model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in response.data]
