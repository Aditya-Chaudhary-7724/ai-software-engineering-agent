"""Pure retrieval-quality metric functions.

Every function here takes plain lists/sets and returns a float — no
dependency on PostgreSQL, Neo4j, or any retrieval code, so these are
unit-tested directly with hand-constructed inputs (see
tests/evaluation/test_metrics.py) rather than only indirectly through
an end-to-end retrieval run. `runners/retrieval.py` and `runners/rag.py`
are what actually call these against real retrieval results.
"""

from typing import AbstractSet, Iterable, List, Sequence, Tuple


def recall_at_k(retrieved: Sequence[str], relevant: AbstractSet[str], k: int) -> float:
    """Fraction of the relevant set found within the top `k` retrieved
    items. Requires a non-empty `relevant` set — recall is undefined
    against an empty ground truth.
    """
    if not relevant:
        raise ValueError("relevant set must be non-empty to compute recall")
    top_k = retrieved[:k]
    hits = len(set(top_k) & relevant)
    return hits / len(relevant)


def precision_at_k(retrieved: Sequence[str], relevant: AbstractSet[str], k: int) -> float:
    """Fraction of the top `k` retrieved items that are actually
    relevant. 0.0 (not undefined) when nothing was retrieved, since
    "retrieved nothing" is a legitimate, scoreable outcome here.
    """
    top_k = retrieved[:k]
    if not top_k:
        return 0.0
    hits = len(set(top_k) & relevant)
    return hits / len(top_k)


def reciprocal_rank(retrieved: Sequence[str], relevant: AbstractSet[str]) -> float:
    """1/rank of the first relevant item found, 0.0 if none is found
    anywhere in `retrieved`.
    """
    for rank, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def mean_reciprocal_rank(cases: Iterable[Tuple[Sequence[str], AbstractSet[str]]]) -> float:
    """Mean reciprocal rank over several (retrieved, relevant) pairs —
    typically one pair per evaluation case in a category, aggregated
    into one MRR@category figure. 0.0 for an empty input, since MRR
    over zero cases is not a meaningful average (never divides by zero).
    """
    ranks: List[float] = [reciprocal_rank(retrieved, relevant) for retrieved, relevant in cases]
    if not ranks:
        return 0.0
    return sum(ranks) / len(ranks)


def hit_rate(retrieved: Sequence[str], relevant: AbstractSet[str]) -> float:
    """1.0 if at least one relevant item appears anywhere in
    `retrieved`, else 0.0 — the loosest useful retrieval signal ("did we
    surface anything relevant at all"), independent of rank or count.
    """
    return 1.0 if set(retrieved) & relevant else 0.0
