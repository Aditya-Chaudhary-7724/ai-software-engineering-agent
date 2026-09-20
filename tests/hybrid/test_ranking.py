from vectorstore.models import SearchResult

from rag.models import Candidate as KeywordCandidate

from hybrid.models import HybridCandidate
from hybrid.ranking import merge_and_rank


def _vector_hit(chunk_id, distance) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id, relative_path="a.py", language="Python", chunk_type="function",
        symbol_name="f", qualified_name="f", start_line=1, end_line=2, content="...", distance=distance,
    )


def _keyword_hit(chunk_id, score) -> KeywordCandidate:
    return KeywordCandidate(
        chunk_id=chunk_id, relative_path="a.py", language="Python", chunk_type="function",
        symbol_name="f", qualified_name="f", start_line=1, end_line=2, content="...",
        vector_similarity=0.0, keyword_score=score, combined_score=0.0,
    )


def _graph_hit(chunk_id, score, relationship="dependency_symbol") -> HybridCandidate:
    return HybridCandidate(
        chunk_id=chunk_id, relative_path="b.py", language="Python", chunk_type="function",
        symbol_name="g", qualified_name="g", start_line=1, end_line=2, content="...",
        vector_similarity=0.0, keyword_score=0.0, graph_score=score, combined_score=0.0,
        found_via=(f"graph:{relationship}",),
    )


def test_vector_only_hit():
    results = merge_and_rank([_vector_hit(1, distance=0.0)], [], [], top_k=5)
    assert results[0].found_via == ("vector",)
    assert results[0].vector_similarity == 1.0


def test_graph_only_hit_ranks_below_vector_hit_by_default_weights():
    vector_hits = [_vector_hit(1, distance=0.0)]
    graph_hits = [_graph_hit(2, score=1.0)]

    results = merge_and_rank(vector_hits, [], graph_hits, top_k=5)

    assert [r.chunk_id for r in results] == [1, 2]


def test_chunk_found_by_all_three_methods_combines_scores():
    vector_hits = [_vector_hit(1, distance=0.2)]
    keyword_hits = [_keyword_hit(1, score=0.4)]
    graph_hits = [_graph_hit(1, score=0.5)]

    results = merge_and_rank(vector_hits, keyword_hits, graph_hits, top_k=5)

    assert len(results) == 1
    assert set(results[0].found_via) == {"vector", "keyword", "graph:dependency_symbol"}
    expected = 0.5 * 0.8 + 0.2 * 0.4 + 0.3 * 0.5
    assert abs(results[0].combined_score - expected) < 1e-9


def test_found_via_accumulates_across_all_sources():
    vector_hits = [_vector_hit(1, distance=0.0)]
    keyword_hits = [_keyword_hit(1, score=0.3)]

    results = merge_and_rank(vector_hits, keyword_hits, [], top_k=5)

    assert set(results[0].found_via) == {"vector", "keyword"}


def test_top_k_truncates():
    vector_hits = [_vector_hit(i, distance=i / 10) for i in range(10)]
    results = merge_and_rank(vector_hits, [], [], top_k=3)
    assert len(results) == 3


def test_results_sorted_descending():
    vector_hits = [_vector_hit(1, distance=0.9), _vector_hit(2, distance=0.1)]
    results = merge_and_rank(vector_hits, [], [], top_k=5)
    assert [r.chunk_id for r in results] == [2, 1]
