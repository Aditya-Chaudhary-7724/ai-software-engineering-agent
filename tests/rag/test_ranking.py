from vectorstore.models import SearchResult

from rag.models import Candidate
from rag.ranking import merge_and_rank


def _search_result(chunk_id, distance, symbol_name="foo") -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        relative_path="main.py",
        language="Python",
        chunk_type="function",
        symbol_name=symbol_name,
        qualified_name=symbol_name,
        start_line=1,
        end_line=2,
        content="...",
        distance=distance,
    )


def _keyword_candidate(chunk_id, keyword_score, symbol_name="foo") -> Candidate:
    return Candidate(
        chunk_id=chunk_id,
        relative_path="main.py",
        language="Python",
        chunk_type="function",
        symbol_name=symbol_name,
        qualified_name=symbol_name,
        start_line=1,
        end_line=2,
        content="...",
        vector_similarity=0.0,
        keyword_score=keyword_score,
        combined_score=0.0,
    )


def test_vector_only_hit_gets_zero_keyword_score():
    results = merge_and_rank([_search_result(1, distance=0.0)], [], top_k=5)

    assert results[0].chunk_id == 1
    assert results[0].vector_similarity == 1.0
    assert results[0].keyword_score == 0.0


def test_keyword_only_hit_gets_zero_vector_similarity():
    results = merge_and_rank([], [_keyword_candidate(2, keyword_score=0.5)], top_k=5)

    assert results[0].chunk_id == 2
    assert results[0].vector_similarity == 0.0
    assert results[0].keyword_score == 0.5


def test_chunk_found_by_both_methods_combines_scores():
    vector_hits = [_search_result(3, distance=0.2)]
    keyword_hits = [_keyword_candidate(3, keyword_score=0.4)]

    results = merge_and_rank(vector_hits, keyword_hits, top_k=5, vector_weight=0.7, keyword_weight=0.3)

    assert len(results) == 1
    expected_similarity = 0.8  # 1.0 - 0.2
    expected_score = 0.7 * expected_similarity + 0.3 * 0.4
    assert results[0].combined_score == expected_score


def test_negative_similarity_is_clamped_to_zero():
    results = merge_and_rank([_search_result(1, distance=1.9)], [], top_k=5)

    assert results[0].vector_similarity == 0.0


def test_results_are_sorted_by_combined_score_descending():
    vector_hits = [_search_result(1, distance=0.9, symbol_name="weak"), _search_result(2, distance=0.1, symbol_name="strong")]

    results = merge_and_rank(vector_hits, [], top_k=5)

    assert [r.chunk_id for r in results] == [2, 1]


def test_top_k_truncates_results():
    vector_hits = [_search_result(i, distance=i / 10, symbol_name=f"f{i}") for i in range(10)]

    results = merge_and_rank(vector_hits, [], top_k=3)

    assert len(results) == 3
