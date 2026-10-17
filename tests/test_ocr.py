"""OCR layout logic on synthetic boxes: no model needed (SPEC §6.3)."""

import random

import pytest

from vault.config import Config
from vault.ingest.ocr import Tiling, blocks, fix_digits, lines_text, merge_strips, plan_tiles, reading_order
from vault.ingest.records import OcrLine

CFG = Config()
H = 20  # line height of the synthetic lines; a paragraph's lines sit 26 apart (gap 6 < 1.5 × 20)


def L(text: str, x0: float, y0: float, x1: float | None = None, y1: float | None = None, conf: float = 0.9) -> OcrLine:
    return OcrLine(text, conf, x0, y0, x1 if x1 is not None else x0 + 10 * len(text), y1 if y1 is not None else y0 + H)


def column(prefix: str, x: float, y: float, n: int, pitch: float = 26) -> list[OcrLine]:
    return [L(f"{prefix}{i}", x, y + i * pitch, x + 300) for i in range(n)]


# ---------------------------------------------------------------- tiling


def test_small_image_is_one_tile() -> None:
    assert plan_tiles(1920, 1080, CFG) == Tiling((1920, 1080), [(0, 1080)])


def test_wide_image_is_scaled_to_max_width() -> None:
    assert plan_tiles(4000, 3000, CFG) == Tiling((2048, 1536), [(0, 1536)])


def test_tall_screenshot_is_cut_into_even_strips() -> None:
    t = plan_tiles(1080, 6000, CFG)
    assert t.size == (1080, 6000)
    assert t.strips == [(0, 1600), (1100, 2700), (2200, 3800), (3300, 4900), (4400, 6000)]


def test_wide_and_tall_is_scaled_then_cut() -> None:
    t = plan_tiles(3000, 6000, CFG)
    assert t.size == (2048, 4096) and len(t.strips) == 3 and t.strips[-1][1] == 4096


def test_too_tall_is_scaled_to_fit_max_strips() -> None:
    t = plan_tiles(1080, 40000, CFG)
    assert len(t.strips) == CFG.ocr_max_strips
    assert t.size[1] == 21200 and t.strips[-1][1] == 21200


@pytest.mark.parametrize("seed", range(20))
def test_strips_cover_with_enough_overlap(seed: int) -> None:
    rnd = random.Random(seed)
    w, h = rnd.randint(200, 5000), rnd.randint(2049, 60000)
    t = plan_tiles(w, h, CFG)
    assert t.size[0] <= CFG.ocr_max_side and len(t.strips) <= CFG.ocr_max_strips
    assert t.strips[0][0] == 0 and t.strips[-1][1] == t.size[1]
    for (_, b), (c, _) in zip(t.strips, t.strips[1:]):
        assert b - c >= CFG.ocr_strip_overlap
    if len(t.strips) > 1:
        assert all(b - a == CFG.ocr_strip for a, b in t.strips)
    else:
        assert t.size[1] <= CFG.ocr_max_side


# ---------------------------------------------------------------- digit fixes


@pytest.mark.parametrize("raw, fixed", [
    ("Error code: Ox80070005.", "Error code: 0x80070005."),
    ("ox1F2E3D", "0x1F2E3D"),
    ("OX8007000E", "0X8007000E"),
    ("build 2O25", "build 2025"),
    ("8OO7", "8007"),
    ("Ox8OO70005", "0x80070005"),
])
def test_fix_digits(raw: str, fixed: str) -> None:
    assert fix_digits(raw) == fixed


@pytest.mark.parametrize("text", ["Oxford", "BOX12345", "Ox12", "O2 sensor", "COVID-19", "OK 200", "Oxen"])
def test_fix_digits_leaves_words_alone(text: str) -> None:
    assert fix_digits(text) == text


# ---------------------------------------------------------------- merging strips


TALL = Tiling((1080, 3000), [(0, 1600), (1400, 3000)])


def test_low_confidence_lines_are_dropped() -> None:
    out = merge_strips([[L("keep", 10, 100), L("noise", 10, 200, conf=0.3)], []], TALL, (1080, 3000), CFG)
    assert [l.text for l in out] == ["keep"]


def test_line_cut_by_inner_edge_comes_from_the_neighbour() -> None:
    cut = L("half", 10, 1585, 100, 1600)  # strip 0 sees only its top
    whole = L("whole line", 10, 185, 200, 215)  # strip 1 (top 1400) sees it at 1585..1615
    out = merge_strips([[cut], [whole]], TALL, (1080, 3000), CFG)
    assert [(l.text, l.y0, l.y1) for l in out] == [("whole line", 1585, 1615)]


def test_overlap_duplicate_is_kept_once_with_best_confidence() -> None:
    a = L("Settings saved", 40, 1450, 300, 1470, conf=0.80)
    b = L("Settings saved", 41, 51, 301, 71, conf=0.95)  # same line, strip 1 coordinates
    out = merge_strips([[a], [b]], TALL, (1080, 3000), CFG)
    assert len(out) == 1 and out[0].conf == 0.95 and out[0].y0 == 1451


def test_identical_words_side_by_side_are_not_duplicates() -> None:
    row = [L("OK", 100, 1450, 140, 1470), L("OK", 400, 1450, 440, 1470)]
    again = [L("OK", 100, 50, 140, 70), L("OK", 400, 50, 440, 70)]
    out = merge_strips([row, again], TALL, (1080, 3000), CFG)
    assert sorted(l.x0 for l in out) == [100, 400]


def test_boxes_map_back_through_scaling_and_text_is_cleaned() -> None:
    t = Tiling((2048, 1000), [(0, 1000)])  # original was 4096 × 2000
    out = merge_strips([[L("code  Ox80070005", 100, 50, 300, 70)]], t, (4096, 2000), CFG)
    assert (out[0].text, out[0].x0, out[0].x1, out[0].y0, out[0].y1) == ("code 0x80070005", 200, 600, 100, 140)


# ---------------------------------------------------------------- reading order and blocks


def texts(lines) -> list[str]:
    return [l.text for l in lines]


def block_texts(lines) -> list[list[str]]:
    return [texts(b) for b in blocks(lines)]


def test_rows_read_left_to_right_and_paragraph_gap_starts_a_block() -> None:
    lines = [L("world", 200, 101), L("hello", 10, 100), L("next", 10, 126), L("after gap", 10, 200)]
    out = reading_order(lines, CFG)
    assert block_texts(out) == [["hello", "world", "next"], ["after gap"]]
    assert [l.line for l in out] == [0, 1, 2, 3]


def test_two_columns_give_two_blocks_not_interleaved_lines() -> None:
    left, right = column("left", 50, 100, 8), column("right", 600, 100, 8)
    out = reading_order(right + left, CFG)
    assert block_texts(out) == [texts(left), texts(right)]


def test_title_above_two_columns() -> None:
    title = [L("A Wide Title Spanning Both Columns", 50, 40, 850)]
    left, right = column("left", 50, 100, 6), column("right", 600, 100, 6)
    out = reading_order(left + right + title, CFG)
    assert block_texts(out) == [texts(title), texts(left), texts(right)]


def test_paragraph_breaks_aligned_across_columns_do_not_interleave() -> None:
    l1, l2 = column("l1-", 50, 100, 4), column("l2-", 50, 250, 4)
    r1, r2 = column("r1-", 600, 100, 4), column("r2-", 600, 250, 4)
    out = reading_order(l1 + l2 + r1 + r2, CFG)
    assert texts(out) == texts(l1 + l2 + r1 + r2)
    assert block_texts(out) == [texts(l1), texts(l2), texts(r1), texts(r2)]


def test_single_row_of_word_boxes_is_not_split_into_columns() -> None:
    words = [L("Error", 10, 50), L("code:", 120, 50), L("0x80070005", 260, 50)]
    out = reading_order(words[::-1], CFG)
    assert block_texts(out) == [["Error", "code:", "0x80070005"]]


def test_lines_text_matches_line_texts() -> None:
    lines = reading_order([L("one", 0, 0), L("two", 0, 26)], CFG)
    assert lines_text(lines) == "one two"


def test_empty() -> None:
    assert reading_order([], CFG) == [] and blocks([]) == []
