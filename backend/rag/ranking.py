"""Merges vector and keyword retrieval results into one ranked list.

Scoring, deliberately simple for Phase 4 (documented as a
simplification; Phase 6's hybrid retrieval is expected to refine this
with graph evidence added to the mix):

- vector_similarity = max(0.0, 1.0 - cosine_distance), so it's in [0, 1]
  (pgvector's cosine distance `<=>` ranges [0, 2]; negative similarity
  is clamped to 0 rather than allowed to cancel out a keyword match).
- keyword_score is PostgreSQL's raw `ts_rank` value, used as-is. It is
  not strictly bounded to [0, 1], which is a known limitation of this
  simple linear combination.
- combined_score = vector_weight * vector_similarity + keyword_weight * keyword_score.

A chunk found by only one method gets 0.0 for the other, rather than
being excluded — a strong keyword-only match (e.g. an exact function
name) should still be able to surface even with no vector hit.
"""

from vectorstore.models import SearchResult

from rag.models import Candidate

DEFAULT_VECTOR_WEIGHT = 0.7
DEFAULT_KEYWORD_WEIGHT = 0.3


def _vector_similarity(distance: float) -> float:
    return max(0.0, 1.0 - distance)


def merge_and_rank(
    vector_hits: list[SearchResult],
    keyword_hits: list[Candidate],
    top_k: int,
    vector_weight: float = DEFAULT_VECTOR_WEIGHT,
    keyword_weight: float = DEFAULT_KEYWORD_WEIGHT,
) -> list[Candidate]:
    merged: dict[int, Candidate] = {}

    for hit in vector_hits:
        merged[hit.chunk_id] = Candidate(
            chunk_id=hit.chunk_id,
            relative_path=hit.relative_path,
            language=hit.language,
            chunk_type=hit.chunk_type,
            symbol_name=hit.symbol_name,
            qualified_name=hit.qualified_name,
            start_line=hit.start_line,
            end_line=hit.end_line,
            content=hit.content,
            vector_similarity=_vector_similarity(hit.distance),
            keyword_score=0.0,
            combined_score=0.0,
        )

    for kw_hit in keyword_hits:
        existing = merged.get(kw_hit.chunk_id)
        vector_similarity = existing.vector_similarity if existing else 0.0
        merged[kw_hit.chunk_id] = Candidate(
            chunk_id=kw_hit.chunk_id,
            relative_path=kw_hit.relative_path,
            language=kw_hit.language,
            chunk_type=kw_hit.chunk_type,
            symbol_name=kw_hit.symbol_name,
            qualified_name=kw_hit.qualified_name,
            start_line=kw_hit.start_line,
            end_line=kw_hit.end_line,
            content=kw_hit.content,
            vector_similarity=vector_similarity,
            keyword_score=kw_hit.keyword_score,
            combined_score=0.0,
        )

    scored = [
        Candidate(
            chunk_id=c.chunk_id,
            relative_path=c.relative_path,
            language=c.language,
            chunk_type=c.chunk_type,
            symbol_name=c.symbol_name,
            qualified_name=c.qualified_name,
            start_line=c.start_line,
            end_line=c.end_line,
            content=c.content,
            vector_similarity=c.vector_similarity,
            keyword_score=c.keyword_score,
            combined_score=vector_weight * c.vector_similarity + keyword_weight * c.keyword_score,
        )
        for c in merged.values()
    ]

    scored.sort(key=lambda c: c.combined_score, reverse=True)
    return scored[:top_k]
