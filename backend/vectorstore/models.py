"""Structured models for code chunks and retrieval results.

The embedding dimension (1536) matches OpenAI's text-embedding-3-small
model, which is the default production embedding provider. Any
embedding provider used with this store must produce vectors of this
same dimension, since it is fixed in the database column type
(`vector(1536)`, see schema.sql).
"""

from dataclasses import dataclass
from typing import Optional

EMBEDDING_DIMENSION = 1536


@dataclass(frozen=True)
class CodeChunk:
    """A unit of source code to be embedded and indexed."""

    relative_path: str
    language: Optional[str]
    chunk_type: str  # "function" | "class" | "file"
    symbol_name: Optional[str]
    qualified_name: Optional[str]
    start_line: int
    end_line: int
    content: str


@dataclass(frozen=True)
class SearchResult:
    """A single similarity search hit."""

    chunk_id: int
    relative_path: str
    language: Optional[str]
    chunk_type: str
    symbol_name: Optional[str]
    qualified_name: Optional[str]
    start_line: int
    end_line: int
    content: str
    distance: float
