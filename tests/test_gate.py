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
    scores = np.linspace(0.1, 0.3, n)
    g = gate(ids(n), scores, Q, CFG)
    assert not g.gated and g.candidates == n
    assert [f for f, _ in g.hits] == list(ids(n)[::-1][:5])


def test_ties_keep_file_id_order() -> None:
    g = gate(ids(3), np.array([0.2, 0.3, 0.3]), Q, CFG)
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
