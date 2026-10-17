import pytest

from vault.search.fuse import collapse, rrf
from vault.search.retrievers import Hit


def test_collapse_keeps_best_chunk_per_file() -> None:
    hits = [Hit(10, 1, 9.0), Hit(11, 2, 8.0), Hit(12, 1, 7.0), Hit(13, 3, 6.0)]
    assert collapse(hits) == [(1, 10), (2, 11), (3, 13)]


def test_rrf_arithmetic_and_missing_lists() -> None:
    fused = rrf({"bm25": [(1, 10), (2, 20)], "dense": [(2, 21), (3, 30)]}, k=60)
    by_id = {f.file_id: f for f in fused}
    assert by_id[2].score == pytest.approx(1 / 62 + 1 / 61)
    assert by_id[1].score == pytest.approx(1 / 61)  # missing from dense: contributes 0
    assert [f.file_id for f in fused] == [2, 1, 3]
    assert by_id[2].ranks == {"bm25": 2, "dense": 1} and by_id[2].chunks == {"bm25": 20, "dense": 21}


def test_rrf_weights_and_ties() -> None:
    fused = rrf({"bm25": [(1, 10)], "dense": [(2, 20)]}, k=60, weights={"bm25": 0.5})
    assert [f.file_id for f in fused] == [2, 1]
    tie = rrf({"bm25": [(5, 50)], "dense": [(4, 40)]}, k=60)
    assert [f.file_id for f in tie] == [4, 5]  # equal score and rank: lower id first, deterministic
