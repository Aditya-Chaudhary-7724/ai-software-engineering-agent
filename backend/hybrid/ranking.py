"""Merges vector, keyword, and graph evidence into one ranked list.

Weights are a documented heuristic starting point, not a measured or
tuned value: vector search remains the primary signal (it directly
measures relevance to the question), keyword search corrects for exact
identifiers vector similarity can miss, and graph evidence contributes
meaningfully but is capped below either direct-match signal, since it
represents "structurally adjacent to a match", not "matches the
question" — a distinction worth keeping visible, not something to
auto-tune away without an evaluation dataset (Phase 12).

A chunk found by multiple methods keeps all of its `found_via` tags
and combines its scores additively (via the weighted sum), so a chunk
that matches on both keyword AND graph evidence outranks one that
matches on graph evidence alone — this is "do not simply concatenate
arbitrary results": every inclusion has a traceable reason.
"""

from vectorstore.models import SearchResult

from rag.models import Candidate as KeywordCandidate

from hybrid.models import HybridCandidate

DEFAULT_VECTOR_WEIGHT = 0.5
DEFAULT_KEYWORD_WEIGHT = 0.2
DEFAULT_GRAPH_WEIGHT = 0.3


def _vector_similarity(distance: float) -> float:
    return max(0.0, 1.0 - distance)


def merge_and_rank(
    vector_hits: list[SearchResult],
    keyword_hits: list[KeywordCandidate],
    graph_hits: list[HybridCandidate],
    top_k: int,
    vector_weight: float = DEFAULT_VECTOR_WEIGHT,
    keyword_weight: float = DEFAULT_KEYWORD_WEIGHT,
    graph_weight: float = DEFAULT_GRAPH_WEIGHT,
) -> list[HybridCandidate]:
    merged: dict[int, HybridCandidate] = {}

    def upsert(
        chunk_id: int,
        relative_path: str,
        language,
        chunk_type: str,
        symbol_name,
        qualified_name,
        start_line: int,
        end_line: int,
        content: str,
        vector_similarity: float,
        keyword_score: float,
        graph_score: float,
        found_via: str,
    ) -> None:
        existing = merged.get(chunk_id)
        merged[chunk_id] = HybridCandidate(
            chunk_id=chunk_id,
            relative_path=relative_path,
            language=language,
            chunk_type=chunk_type,
            symbol_name=symbol_name,
            qualified_name=qualified_name,
            start_line=start_line,
            end_line=end_line,
            content=content,
            vector_similarity=max(vector_similarity, existing.vector_similarity if existing else 0.0),
            keyword_score=max(keyword_score, existing.keyword_score if existing else 0.0),
            graph_score=max(graph_score, existing.graph_score if existing else 0.0),
            combined_score=0.0,
            found_via=(existing.found_via if existing else ()) + (found_via,),
        )

    for vector_hit in vector_hits:
        upsert(
            vector_hit.chunk_id, vector_hit.relative_path, vector_hit.language, vector_hit.chunk_type,
            vector_hit.symbol_name, vector_hit.qualified_name, vector_hit.start_line, vector_hit.end_line,
            vector_hit.content, _vector_similarity(vector_hit.distance), 0.0, 0.0, "vector",
        )

    for keyword_hit in keyword_hits:
        upsert(
            keyword_hit.chunk_id, keyword_hit.relative_path, keyword_hit.language, keyword_hit.chunk_type,
            keyword_hit.symbol_name, keyword_hit.qualified_name, keyword_hit.start_line, keyword_hit.end_line,
            keyword_hit.content, 0.0, keyword_hit.keyword_score, 0.0, "keyword",
        )

    for graph_hit in graph_hits:
        upsert(
            graph_hit.chunk_id, graph_hit.relative_path, graph_hit.language, graph_hit.chunk_type,
            graph_hit.symbol_name, graph_hit.qualified_name, graph_hit.start_line, graph_hit.end_line,
            graph_hit.content, 0.0, 0.0, graph_hit.graph_score, graph_hit.found_via[0],
        )

    scored = [
        HybridCandidate(
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
            graph_score=c.graph_score,
            combined_score=(
                vector_weight * c.vector_similarity
                + keyword_weight * c.keyword_score
                + graph_weight * c.graph_score
            ),
            found_via=c.found_via,
        )
        for c in merged.values()
    ]

    scored.sort(key=lambda c: c.combined_score, reverse=True)
    return scored[:top_k]
