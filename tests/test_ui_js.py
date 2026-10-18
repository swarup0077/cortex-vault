"""cortex.js decisions (SPEC §8), run in Node: search as you type, Enter, Esc, chips and "Show more".

Skipped where Node isn't installed; the browser behaviour itself is checked by hand (design/README.md).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "vault" / "ui" / "static" / "cortex.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


def run(expr: str):
    code = f"const C = require({json.dumps(str(JS))}); process.stdout.write(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True)
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


def test_esc_closes_the_phone_sheet_first_and_otherwise_clears_the_box() -> None:
    assert run("[C.escAction(true, 'dice'), C.escAction(false, 'dice'), C.escAction(false, '  ')]") == \
        ["close-sheet", "clear", "none"]


def test_chips_and_show_more_keep_the_top_ranked_wherever_their_group_is() -> None:
    # display order: 25 close matches (fused ranks 0, 2-25), then a visual match at fused rank 1, then 4 related
    kinds = ["pdf"] * 25 + ["photo"] + ["doc"] * 4
    ranks = [0, *range(2, 26), 1, *range(26, 30)]
    out = run(f"C.visibility({json.dumps(kinds)}, {json.dumps(ranks)}, 'all', false, 20)")
    assert sum(out["visible"]) == 20 and out["visible"][25] is True and out["more"] == 10
    out = run(f"C.visibility({json.dumps(kinds)}, {json.dumps(ranks)}, 'doc', false, 20)")
    assert out["visible"][26:] == [True] * 4 and sum(out["visible"]) == 4 and out["more"] == 0
    out = run(f"C.visibility({json.dumps(kinds)}, {json.dumps(ranks)}, 'pdf', true, 20)")
    assert sum(out["visible"]) == 25 and out["more"] == 0


def test_normalize_matches_the_server() -> None:
    from vault.ui.layout import normalize

    for text in ["  error   0x80070005 ", "dice\ton a\ntable", ""]:
        assert run(f"C.normalize({json.dumps(text)})") == normalize(text)
