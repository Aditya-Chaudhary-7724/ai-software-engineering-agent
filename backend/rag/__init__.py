"""Code RAG pipeline (Phase 4).

Question -> query embedding -> vector retrieval + keyword search ->
metadata filtering -> ranking -> bounded context assembly -> LLM ->
grounded response with source citations built from retrieval metadata.
"""

from rag.models import Candidate, RAGAnswer, Source
from rag.service import RAGService

__all__ = ["Candidate", "RAGAnswer", "Source", "RAGService"]
