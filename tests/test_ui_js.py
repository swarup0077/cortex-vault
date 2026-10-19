"""cortex.js decisions (SPEC §8), run in Node: search as you type, Enter, Esc, the type filter and "Show more", the
count line, recent searches, and the upload bar.

Skipped where Node isn't installed; the browser behaviour itself is checked by hand in headless Edge.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "vault" / "ui" / "static" / "cortex.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


def run(expr: str):
    code = f"const C = require({json.dumps(str(JS))}); process.stdout.write(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def test_typing_searches_from_two_characters_and_only_when_the_text_changed() -> None:
    assert run("[C.searchFor('a', ''), C.searchFor(' ab ', ''), C.searchFor('ab', 'ab'), C.searchFor('a  b', 'a b')]") \
        == [None, "ab", None, None]
    assert run("[C.searchFor('', 'ab'), C.searchFor('   ', ''), C.MIN_CHARS, C.DELAY_MS]") == ["", None, 2, 300]


def test_enter_opens_only_when_the_results_belong_to_the_text_in_the_box() -> None:
    """Owner's rule: otherwise it runs the search right away and opens nothing."""
    assert run("C.enterAction('error 0x80070005', 'error 0x80070005', true)") == "open"
    assert run("C.enterAction('  error   0x80070005 ', 'error 0x80070005', true)") == "open"  # same, normalised
    assert run("C.enterAction('error 0x8007000', 'error 0x80070005', true)") == "search"  # still typing
    assert run("C.enterAction('error 0x80070005', null, false)") == "search"  # nothing on screen yet
    assert run("C.enterAction('error 0x80070005', 'error 0x80070005', false)") == "none"  # no result to open
    assert run("C.enterAction('e', 'error', true)") == "none"  # too short to search


def test_esc_closes_the_drawer_or_sheet_first_and_otherwise_clears_the_box() -> None:
    assert run("[C.escAction(true, 'dice'), C.escAction(false, 'dice'), C.escAction(false, '  ')]") == \
        ["close-sheet", "clear", "none"]


def test_the_type_filter_and_show_more_keep_the_top_ranked() -> None:
    # list order is fused order: 25 PDFs, a photo, then 4 weaker docs
    types = ["pdf"] * 25 + ["photo"] + ["doc"] * 4
    ranks = list(range(30))
    out = run(f"C.visibility({json.dumps(types)}, {json.dumps(ranks)}, 'all', false, 20)")
    assert out["visible"] == [True] * 20 + [False] * 10 and out["more"] == 10
    out = run(f"C.visibility({json.dumps(types)}, {json.dumps(ranks)}, 'doc', false, 20)")
    assert out["visible"][26:] == [True] * 4 and sum(out["visible"]) == 4 and out["more"] == 0
    out = run(f"C.visibility({json.dumps(types)}, {json.dumps(ranks)}, 'photo', false, 20)")
    assert out["visible"][25] is True and sum(out["visible"]) == 1  # the photo at #26 shows under its filter
    out = run(f"C.visibility({json.dumps(types)}, {json.dumps(ranks)}, 'pdf', true, 20)")
    assert sum(out["visible"]) == 25 and out["more"] == 0


def test_the_count_line_says_when_a_filter_is_on_and_matches_the_server() -> None:
    from vault.ui.views import TYPE_PLURALS, count_words

    cases = [(14, 36, ""), (1, 0, ""), (3, 0, "Photos"), (0, 2, "PDFs"), (1, 1, "Text files")]
    assert run("[" + ", ".join(f"C.countWords({n}, {w}, {json.dumps(label)})" for n, w, label in cases) + "]") == \
        [count_words(n, w, label) for n, w, label in cases]
    assert run("C.countWords(3, 0, C.PLURALS.photo)") == "matches in Photos"
    assert run("C.PLURALS") == TYPE_PLURALS


def test_recent_searches_keep_the_newest_five_once_each() -> None:
    assert run("C.remember(['dice', 'sunset'], '  error   0x80070005 ')") == ["error 0x80070005", "dice", "sunset"]
    assert run("C.remember(['dice', 'sunset'], 'Sunset')") == ["Sunset", "dice"]  # once, case aside
    assert run("C.remember(['a1', 'a2', 'a3', 'a4', 'a5'], 'new')") == ["new", "a1", "a2", "a3", "a4"]
    assert run("C.remember(['dice'], 'x')") == ["dice"]  # too short to have been a search
    assert run("C.forget(['dice', 'sunset'], 'dice')") == ["sunset"]
    assert run("[C.SAVE_MS, C.RECENT_MAX]") == [1500, 5]


def test_recent_searches_from_storage_are_made_safe() -> None:
    assert run("C.parseRecent(null)") == [] and run("C.parseRecent('not json')") == []
    assert run("C.parseRecent('{\"a\": 1}')") == []
    raw = json.dumps(json.dumps(["dice", 7, "x", " sunset  beach ", "y" * 300, "a", "b1", "c1", "d1", "e1"]))
    assert run(f"C.parseRecent({raw})") == ["dice", "sunset beach", "b1", "c1", "d1"]


def test_normalize_matches_the_server() -> None:
    from vault.ui.layout import normalize

    for text in ["  error   0x80070005 ", "dice\ton a\ntable", ""]:
        assert run(f"C.normalize({json.dumps(text)})") == normalize(text)


def test_the_upload_bar_says_real_numbers_only() -> None:
    """Owner's rule (SPEC §8): the transfer's percentage only when the browser measured it, real counts while the
    server lists and reads, an indeterminate bar (null) where there's no number, and never a time estimate."""
    assert run("C.barText({stage: 'upload', done: 2, total: 10, pct: 27})") == "Uploading 3 of 10 files · 27%"
    assert run("C.barText({stage: 'upload', done: 0, total: 1, pct: null})") == "Uploading 1 of 1 file"
    assert run("C.barText({stage: 'list', done: 0, total: 0})") == "Listing files"
    assert run("C.barText({stage: 'list', done: 4, total: 12})") == "Listing 5 of 12 files"
    assert run("C.barText({stage: 'read', done: 11, total: 12})") == "Reading 12 of 12 · names searchable now"
    assert run("C.barText({stage: 'done', summary: '3 added · 1 not added'})") == "Upload done: 3 added · 1 not added"
    assert run("[C.barValue({stage: 'upload', pct: null}), C.barValue({stage: 'upload', pct: 27}), "
               "C.barValue({stage: 'list', done: 0, total: 0}), C.barValue({stage: 'read', done: 3, total: 12})]") \
        == [None, 0.27, None, 0.25]
    assert run("[C.percent(0, 100), C.percent(999, 1000), C.percent(1000, 1000), C.percent(5, 0)]") == [0, 99, 100, 0]
    body = re.search(r"function barText\(state\) \{(.*?)\n  \}", JS.read_text(encoding="utf-8"), re.S).group(1)
    shown = " ".join(re.findall(r"'[^']*'|`[^`]*`", body))  # the words the bar can display
    assert "Uploading" in shown and not re.search(r"remaining|left|eta|minute|second|\bsec\b|\bmin\b", shown, re.I)


def test_update_results_shows_only_when_the_index_is_newer() -> None:
    assert run("[C.stale('6', 8), C.stale(8, 8), C.stale('', 0), C.stale(9, '8')]") == [True, False, False, False]
