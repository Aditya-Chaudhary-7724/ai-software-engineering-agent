"""LLM provider abstraction.

Every provider (real or stub) implements the same interface so the
rest of the RAG pipeline never depends on which model actually
generated the answer.
"""

from abc import ABC, abstractmethod
from typing import Optional


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        """Return the model's text response to `prompt`."""
