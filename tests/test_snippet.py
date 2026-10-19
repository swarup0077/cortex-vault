"""Snippets (SPEC §7.4) and open item y: a meaning-only match shows text, not a status or title bar."""

import sqlite3

from vault.config import Config
from vault.search.engine import _snippet_chunks
from vault.search.fuse import Fused
from vault.search.snippet import make_snippet, prose_start


def test_a_term_match_is_marked_in_its_window() -> None:
    text = "08:12 87% Notes " + "filler words here " * 20 + "the fume hood is on"
    snip = make_snippet(text, "fume hood")
    assert "<mark>fume</mark> <mark>hood</mark>" in snip and snip.startswith("…")


def test_a_meaning_only_snippet_starts_at_prose_not_at_the_status_or_title_bar() -> None:
    """Generated screens (corpus/make_standin.py): a phone status bar and a dialog's title bar."""
    assert make_snippet("08:12 87% Notes Lab checklist Before the session: switch on the fume hood", "xyz",
                        200).startswith("…Notes Lab checklist Before")
    assert make_snippet("Documents Installer □ X Setup could not complete Error 0x80070005: Access is denied.",
                        "qq").startswith("…Setup could not complete")
    assert make_snippet("08:12 87% Browser", "qq") == "08:12 87% Browser"  # no prose at all: shown as it is
    assert prose_start("Terminal - users $ python manage.py Traceback (most recent call last):") is not None
    assert make_snippet("Plain prose from the start.", "qq") == "Plain prose from the start."


def test_a_meaning_only_match_takes_its_best_dense_chunk_that_reads_as_prose() -> None:
    """The status bar was its own chunk and dense's best for the file; the page's text is the snippet now. A file
    with no prose chunk keeps its best, and a keyword match keeps its own chunk."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, text TEXT)")
    conn.executemany("INSERT INTO chunks VALUES (?, ?)", [
        (1, "08:12 87% Browser"), (2, "How to back up your phone Most phones can copy photos and messages"),
        (3, "0 87% 12"), (4, "a keyword match chunk with prose in it")])
    fused = [Fused(10, 0.1, {"dense": 1}, {"dense": 1}), Fused(11, 0.1, {"dense": 2}, {"dense": 3}),
             Fused(12, 0.1, {"bm25": 1, "dense": 3}, {"bm25": 4, "dense": 1})]
    chosen = _snippet_chunks(conn, fused, {10: [1, 2], 11: [3], 12: [1]}, Config())
    assert chosen == {10: 2, 11: 3, 12: 4}
