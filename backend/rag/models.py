"""Structured models for the RAG pipeline."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Candidate:
    """A retrieval candidate after vector + keyword results are merged.

    `vector_similarity` and `keyword_score` are each 0.0 when the chunk
    was not found by that retrieval method — see ranking.py for how
    they combine into `combined_score`.
    """

    chunk_id: int
    relative_path: str
    language: Optional[str]
    chunk_type: str
    symbol_name: Optional[str]
    qualified_name: Optional[str]
    start_line: int
    end_line: int
    content: str
    vector_similarity: float
    keyword_score: float
    combined_score: float


@dataclass(frozen=True)
class Source:
    """A citation attached to a RAGAnswer, built directly from retrieval
    metadata — not from the LLM's own (unverified) claims about where
    an answer came from.
    """

    relative_path: str
    start_line: int
    end_line: int
    chunk_type: str
    symbol_name: Optional[str]


@dataclass(frozen=True)
class RAGAnswer:
    question: str
    answer: str
    sources: list[Source]
    context_chunk_count: int
