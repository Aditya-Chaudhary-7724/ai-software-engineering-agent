"""Local, open-source embeddings via fastembed (ONNX Runtime; no PyTorch,
no API key, no network calls at embedding time).

Why fastembed over sentence-transformers: it runs ONNX exports on ONNX
Runtime, which ships linux/aarch64 wheels and needs no PyTorch (measured on
linux/arm64: 226 MB of packages; bge-small at batch 8 peaked at 524 MiB).

Pinned weights: the model is ALWAYS loaded from a local directory holding
one exact Hugging Face revision (backend/config.py: FASTEMBED_MODELS):
- EMBEDDING_MODEL_PATH, when set (the production image bakes the model in at
  build time, so the API never downloads anything at runtime), else
- a one-time `snapshot_download(repo, revision=<pinned commit>)` into the
  Hugging Face cache (local development).
fastembed's own downloader is never used: it would fetch the latest files.

Loaded models are cached per process (loading costs ~200 MB and seconds),
so building this provider per request is cheap.

Requires Python >= 3.10 (fastembed 0.9.1). On older interpreters the
package is not installed and selecting this provider is reported as a
configuration error, never a silent fallback.
"""

import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from vectorstore.embeddings.base import EmbeddingProvider
from vectorstore.exceptions import EmbeddingConfigurationError, EmbeddingDimensionError

_MODEL_CACHE: Dict[Tuple[str, str, Optional[int]], Any] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def download_pinned_model(hf_repo: str, hf_revision: str, target_dir: Optional[str] = None) -> str:
    """Downloads exactly `hf_revision` of `hf_repo` and returns its local
    directory. Used at image build time and on first local use."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:  # pragma: no cover - only without fastembed installed
        raise EmbeddingConfigurationError("fastembed is not installed (requires Python >= 3.10)") from exc
    return snapshot_download(repo_id=hf_repo, revision=hf_revision, local_dir=target_dir)


class FastEmbedEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        *,
        model_name: str,
        dimension: int,
        hf_repo: str,
        hf_revision: str,
        batch_size: int,
        model_path: Optional[str] = None,
        threads: Optional[int] = None,
    ) -> None:
        self.model_name = model_name
        self.dimension = dimension
        self._hf_repo = hf_repo
        self._hf_revision = hf_revision
        self._batch_size = batch_size
        self._model_path = model_path
        self._threads = threads

    def _model(self) -> Any:
        key = (self.model_name, self._model_path or self._hf_revision, self._threads)
        with _MODEL_CACHE_LOCK:
            model = _MODEL_CACHE.get(key)
            if model is None:
                model = self._load()
                _MODEL_CACHE[key] = model
            return model

    def _load(self) -> Any:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbeddingConfigurationError("fastembed is not installed (requires Python >= 3.10)") from exc
        if self._model_path:
            if not Path(self._model_path).is_dir():
                raise EmbeddingConfigurationError("EMBEDDING_MODEL_PATH does not exist or is not a directory")
            model_dir = self._model_path
        else:
            model_dir = download_pinned_model(self._hf_repo, self._hf_revision)
        return TextEmbedding(model_name=self.model_name, specific_model_path=model_dir, threads=self._threads)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = [vector.tolist() for vector in self._model().embed(texts, batch_size=self._batch_size)]
        if len(vectors) != len(texts):
            raise EmbeddingDimensionError(f"expected {len(texts)} vectors, got {len(vectors)}")
        for vector in vectors:
            if len(vector) != self.dimension:
                # Never store or search with a vector of the wrong size.
                raise EmbeddingDimensionError(f"{self.model_name} produced {len(vector)} dimensions, expected {self.dimension}")
        return vectors
