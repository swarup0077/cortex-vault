"""Snippets (SPEC §7.4) and open item y: a meaning-only match shows text, not a status or title bar."""

import sqlite3

from vault.config import Config
from vault.search.engine import _snippet_chunks
from vault.search.fuse import Fused
from vault.search.snippet import make_snippet, prose_start


def test_marks_terms_with_crude_stemming() -> None:
    s = make_snippet("The definition of SVD: it is defined for every matrix.", "where is SVD defined")
    assert s == "The <mark>definition</mark> of <mark>SVD</mark>: it is <mark>defined</mark> for every matrix."


def test_escapes_html() -> None:
    s = make_snippet("<b>error</b> & more", "error")
    assert s == "&lt;b&gt;<mark>error</mark>&lt;/b&gt; &amp; more"


def test_window_centres_on_matches() -> None:
    text = ("filler words here " * 30) + "the gradient descent step " + ("more filler " * 30)
    s = make_snippet(text, "gradient descent", width=120)
    assert "<mark>gradient</mark> <mark>descent</mark>" in s
    assert s.startswith("…") and s.endswith("…")
    assert len(s.replace("<mark>", "").replace("</mark>", "")) <= 122


def test_no_match_returns_start() -> None:
    s = make_snippet("alpha beta gamma " * 20, "zeta", width=50)
    assert s.startswith("alpha beta") and s.endswith("…") and "<mark>" not in s


def test_highlight_marks_the_whole_text_and_escapes_it() -> None:
    from vault.search.snippet import highlight

    text = "Gradient <descent> steps " + "x " * 300 + "against the gradient"
    out = highlight(text, "gradient descent")
    assert out.count("<mark>") == 3 and "&lt;<mark>descent</mark>&gt;" in out and out.endswith("<mark>gradient</mark>")


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
