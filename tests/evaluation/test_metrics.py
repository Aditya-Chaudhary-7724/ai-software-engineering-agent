"""Pure unit tests — plain lists/sets, no database or service involved."""

import pytest

from evaluation.metrics import hit_rate, mean_reciprocal_rank, precision_at_k, recall_at_k, reciprocal_rank


def test_recall_at_k_perfect_match():
    assert recall_at_k(["a", "b"], {"a", "b"}, k=2) == 1.0


def test_recall_at_k_partial_match():
    assert recall_at_k(["a", "x"], {"a", "b"}, k=2) == 0.5


def test_recall_at_k_only_considers_top_k():
    assert recall_at_k(["x", "y", "a"], {"a"}, k=2) == 0.0
    assert recall_at_k(["x", "y", "a"], {"a"}, k=3) == 1.0


def test_recall_at_k_raises_on_empty_relevant_set():
    with pytest.raises(ValueError):
        recall_at_k(["a"], set(), k=5)


def test_precision_at_k_perfect_match():
    assert precision_at_k(["a", "b"], {"a", "b"}, k=2) == 1.0


def test_precision_at_k_partial_match():
    assert precision_at_k(["a", "x"], {"a"}, k=2) == 0.5


def test_precision_at_k_returns_zero_for_empty_retrieval():
    assert precision_at_k([], {"a"}, k=5) == 0.0


def test_reciprocal_rank_first_position():
    assert reciprocal_rank(["a", "b"], {"a"}) == 1.0


def test_reciprocal_rank_second_position():
    assert reciprocal_rank(["b", "a"], {"a"}) == 0.5


def test_reciprocal_rank_not_found():
    assert reciprocal_rank(["b", "c"], {"a"}) == 0.0


def test_mean_reciprocal_rank_averages_across_cases():
    cases = [(["a"], {"a"}), (["b", "a"], {"a"}), (["z"], {"a"})]
    assert mean_reciprocal_rank(cases) == pytest.approx((1.0 + 0.5 + 0.0) / 3)


def test_mean_reciprocal_rank_empty_input_is_zero():
    assert mean_reciprocal_rank([]) == 0.0


def test_hit_rate_true_when_any_overlap():
    assert hit_rate(["a", "x"], {"a"}) == 1.0


def test_hit_rate_false_when_no_overlap():
    assert hit_rate(["x", "y"], {"a"}) == 0.0
