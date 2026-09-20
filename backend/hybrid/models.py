"""Structured models for hybrid (vector + keyword + graph) retrieval.

Field names deliberately match rag.models.Candidate's shape (relative_path,
start_line, end_line, content, ...) so HybridCandidate objects can be
passed directly into rag.context.build_context and rag.prompt.build_prompt
without duplicating that logic for a third time.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class HybridCandidate:
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
    graph_score: float
    combined_score: float
    found_via: tuple[str, ...]  # e.g. ("vector", "keyword") or ("graph:parent_class",)


@dataclass(frozen=True)
class HybridSource:
    relative_path: str
    start_line: int
    end_line: int
    chunk_type: str
    symbol_name: Optional[str]
    found_via: tuple[str, ...]


@dataclass(frozen=True)
class HybridAnswer:
    question: str
    answer: str
    sources: list[HybridSource]
    context_chunk_count: int
