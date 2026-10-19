"""CLIP gate on synthetic score arrays (SPEC §7.3, the M3 done-when)."""

import numpy as np
import pytest

from vault.config import Config
from vault.search.gate import gate, is_code_like
from vault.search.parse import ParsedQuery

CFG = Config()
Q = ParsedQuery(text="sunset at the beach")


def ids(n: int) -> np.ndarray:
    return np.arange(100, 100 + n)


def test_one_clear_outlier_is_kept_alone() -> None:
    rng = np.random.default_rng(0)
    scores = rng.uniform(0.20, 0.26, 200)
    scores[37] = 0.34
    g = gate(ids(200), scores, Q, CFG)
    assert g.gated and g.candidates == 200 and [f for f, _ in g.hits] == [137]


def test_no_image_stands_out_so_clip_stays_out() -> None:
    """Uniform scores never reach z = 2 (max z ≈ 1.73): CLIP returns a ranking, the gate keeps none of it."""
    g = gate(ids(100), np.linspace(0.20, 0.30, 100), ParsedQuery(text="error 0x80070005"), CFG)
    assert g.gated and g.hits == []


def test_identical_scores_keep_nothing() -> None:
    g = gate(ids(50), np.full(50, 0.25), Q, CFG)
    assert g.gated and g.hits == []


def test_hits_are_capped_and_best_first() -> None:
    scores = np.full(1000, 0.20)
    scores[:60] = np.linspace(0.40, 0.35, 60)  # 60 outliers, best first
    g = gate(ids(1000), scores, Q, CFG)
    assert len(g.hits) == CFG.clip_gate_max == 50
    assert [f for f, _ in g.hits] == list(range(100, 150))
    assert all(a[1] >= b[1] for a, b in zip(g.hits, g.hits[1:]))


def test_z_threshold_is_inclusive_and_configurable() -> None:
    scores = np.array([1.0] + [0.0] * 24)  # z of the 1.0 is (1 - 0.04) / 0.196 ≈ 4.9
    assert len(gate(ids(25), scores, Q, CFG).hits) == 1
    assert gate(ids(25), scores, Q, Config(clip_gate_z=5.0)).hits == []


@pytest.mark.parametrize("n", [1, 5, 19])
def test_few_candidates_keep_the_top_five_ungated(n: int) -> None:
    scores = np.linspace(0.27, 0.35, n)  # all similar enough
    g = gate(ids(n), scores, Q, CFG)
    assert not g.gated and g.candidates == n
    assert [f for f, _ in g.hits] == list(ids(n)[::-1][:5])


def test_few_candidates_keep_only_hits_above_the_min_cosine() -> None:
    """Open item r: with < 20 images the top 5 used to enter fusion for any query, relevant or not."""
    scores = np.array([0.30, 0.25, 0.20, 0.26])
    assert [f for f, _ in gate(ids(4), scores, Q, CFG).hits] == [100, 103]  # 0.26 is inclusive
    assert gate(ids(4), scores, Q, Config(clip_min_cosine=0.31)).hits == []
    assert len(gate(ids(4), scores, Q, Config(clip_min_cosine=0.0)).hits) == 4


def test_strong_hits_are_the_ones_far_above_the_rest() -> None:
    """The visual-query rule boosts only hits with z >= clip_strong_z; weaker gated hits are kept but not strong."""
    scores = np.full(100, 0.20)
    scores[:3] = [0.40, 0.33, 0.29]  # each leads the next by more than the gap
    g = gate(ids(100), scores, Q, CFG)
    z = (scores - scores.mean()) / scores.std()
    assert [f for f, _ in g.hits] == [100, 101, 102] and all(z[:3] >= CFG.clip_gate_z)
    assert g.strong == frozenset(100 + i for i in range(3) if z[i] >= CFG.clip_strong_z)
    assert gate(ids(100), scores, Q, Config(clip_strong_z=6.0)).strong == {100}  # z ≈ 7.8, 5.0, 3.4
    assert gate(ids(100), scores, Q, Config(clip_strong_z=float(z[2]))).strong == {100, 101, 102}  # inclusive


def test_a_strong_hit_leads_the_next_image_by_the_gap() -> None:
    """Open item z: in a big library a look-alike's z clears the bar for text queries, but it leads the next
    look-alike by little. The lead is inclusive, and a gap of 0 is the z bar alone."""
    scores = np.full(700, 0.20)
    scores[:3] = [0.40, 0.39, 0.30]  # z ≈ 18, 17, 9: all over the z bar
    assert gate(ids(700), scores, Q, CFG).strong == frozenset()  # #1 leads #2 by 0.01
    assert gate(ids(700), scores, Q, Config(clip_strong_gap=0.01)).strong == {100, 101, 102}
    assert gate(ids(700), scores, Q, Config(clip_strong_gap=0.0)).strong == {100, 101, 102}
    scores[1] = 0.36  # #1 leads by 0.04 now, #2 leads #3 by 0.06
    assert gate(ids(700), scores, Q, CFG).strong == {100, 101, 102}


def test_strong_hits_run_from_the_top() -> None:
    """A hit whose lead is too small ends the run: the one below it isn't strong even if it leads its own next,
    so the rule's boost never lifts a hit above a better one."""
    scores = np.full(700, 0.20)
    scores[:3] = [0.40, 0.39, 0.30]  # #2 leads #3 by 0.09, but #1 leads #2 by only 0.01
    assert 101 not in gate(ids(700), scores, Q, CFG).strong
    scores[:3] = [0.45, 0.35, 0.34]  # #1 leads by 0.10, #2 by only 0.01: #1 alone
    assert gate(ids(700), scores, Q, CFG).strong == {100}


def test_the_gap_leaves_the_small_library_rule_alone() -> None:
    """Under 20 images there's no z-score; strong is cosine >= clip_strong_cosine, whatever the lead."""
    scores = np.array([0.31, 0.30, 0.20])
    assert gate(ids(3), scores, Q, CFG).strong == {100, 101}


def test_few_candidates_are_strong_by_cosine() -> None:
    scores = np.array([0.31, 0.29, 0.27, 0.20])
    g = gate(ids(4), scores, Q, CFG)
    assert [f for f, _ in g.hits] == [100, 101, 102]  # >= clip_min_cosine
    assert g.strong == {f for f, s in g.hits if s >= CFG.clip_strong_cosine}
    assert gate(ids(4), scores, Q, Config(clip_strong_cosine=0.30)).strong == {100}


def test_ties_keep_file_id_order() -> None:
    g = gate(ids(3), np.array([0.27, 0.3, 0.3]), Q, CFG)
    assert [f for f, _ in g.hits] == [101, 102, 100]


def test_no_candidates() -> None:
    g = gate(np.zeros(0, np.int64), np.zeros(0), Q, CFG)
    assert g.hits == [] and g.candidates == 0


@pytest.mark.parametrize("text, code_like", [
    ("error 0x80070005", True), ("0X1F2E", True), ("ticket 1234567", True), ("PNR AB12CD", True),
    ("covid19 rules", True), ("abc1", True),
    ("sunset at the beach", False), ("route 66", False), ("in 2025", False), ("mp3", False), ("12345", False),
])
def test_code_like(text: str, code_like: bool) -> None:
    assert is_code_like(text) is code_like


def test_code_like_or_quoted_query_halves_clip_weight() -> None:
    scores = np.array([0.3, 0.2])
    assert gate(ids(2), scores, Q, CFG).weight_factor == 1.0
    assert gate(ids(2), scores, ParsedQuery(text="error 0x80070005"), CFG).weight_factor == 0.5
    assert gate(ids(2), scores, ParsedQuery(text="beach", phrases=("golden hour",)), CFG).weight_factor == 0.5
