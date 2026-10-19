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


def test_rrf_boost_multiplies_one_file_in_one_list() -> None:
    fused = rrf({"clip": [(1, None), (2, None)], "dense": [(3, 30)]}, k=60, boosts={"clip": {1: 2.0}})
    by_id = {f.file_id: f for f in fused}
    assert by_id[1].score == pytest.approx(2 / 61) and by_id[2].score == pytest.approx(1 / 62)
    assert by_id[1].ranks == {"clip": 1} and by_id[3].score == pytest.approx(1 / 61)


def test_all_terms_list_lifts_the_exact_match() -> None:
    """Owner's pattern (a): screenshot S is BM25 #1 but missing from dense; transcript T is BM25 #2 and dense #1."""
    lists = {"bm25": [(1, 10), (2, 20)], "dense": [(2, 21)]}
    assert [f.file_id for f in rrf(lists, k=60)] == [2, 1]  # 1/62 + 1/61 beats 1/61
    with_all = rrf({**lists, "all_terms": [(1, 10)]}, k=60)
    assert [f.file_id for f in with_all] == [1, 2]  # 2/61 beats 1/62 + 1/61
    assert with_all[0].ranks == {"bm25": 1, "all_terms": 1}


def test_a_contents_page_stands_for_its_file_only_when_nothing_else_does() -> None:
    """Open item n: the file's rank in a list comes from its best chunk that isn't a contents page, if any."""
    from vault.search.fuse import collapse
    from vault.search.retrievers import Hit

    hits = [Hit(1, 10, 9.0, toc=True), Hit(2, 11, 8.0), Hit(3, 10, 7.0), Hit(4, 12, 6.0, toc=True)]
    assert collapse(hits) == [(11, 2), (10, 3), (12, 4)]
    assert collapse([Hit(1, 10, 9.0), Hit(2, 10, 8.0)]) == [(10, 1)]  # no contents page: as before
