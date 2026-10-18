"""Query parsing: the M4 kind words (SPEC §7.1), shared with the UI's kind chips."""

import pytest

from vault.search.parse import KIND_LABELS, KIND_WORDS, ParsedQuery, parse


@pytest.mark.parametrize(
    ("query", "kinds", "text"),
    [
        ("screenshot of the access denied error", {"screenshot"}, "of the access denied error"),
        ("Screenshots from the build", {"screenshot"}, "from the build"),
        ("photo of a whiteboard", {"photo"}, "of a whiteboard"),
        ("pictures at the beach", {"photo"}, "at the beach"),
        ("pdf about diffusion", {"pdf"}, "about diffusion"),
        ("PDFs on linear algebra", {"pdf"}, "on linear algebra"),
        ("slide about gradient descent", {"slides"}, "about gradient descent"),
        ("deck on SVD", {"slides"}, "on SVD"),
        ("ppt with the agenda", {"slides"}, "with the agenda"),
        ("doc about the electricity invoice", {"pdf", "doc"}, "about the electricity invoice"),
        ("docs for onboarding", {"pdf", "doc"}, "for onboarding"),
    ],
)
def test_kind_words_become_filters_and_leave_the_text(query, kinds, text) -> None:
    pq = parse(query)
    assert pq.kinds == frozenset(kinds)
    assert pq.text == text


def test_several_kind_words_combine() -> None:
    assert parse("screenshot or photo of a receipt").kinds == {"screenshot", "photo"}


def test_notes_is_not_a_kind_word() -> None:
    """SPEC §7.1, §19 18 Oct: as a pdf + doc filter it would drop every image from 'handwritten notes'."""
    pq = parse("handwritten notes on a black background")
    assert pq.kinds == frozenset()
    assert pq.text == "handwritten notes on a black background"


def test_punctuation_around_a_kind_word() -> None:
    pq = parse("error (screenshot), please")
    assert pq.kinds == {"screenshot"} and pq.text == "error please"


def test_words_that_only_contain_a_kind_word_stay() -> None:
    for query in ("screenshot-like mockup", "photography tips", "pdfium build", "slideshow", "doctor"):
        pq = parse(query)
        assert pq.kinds == frozenset() and pq.text == query


def test_quoted_text_keeps_its_kind_words() -> None:
    pq = parse('"export to pdf failed" screenshot')
    assert pq.kinds == {"screenshot"}
    assert pq.text == '"export to pdf failed"'


def test_only_a_kind_word_is_a_filter_only_search() -> None:
    pq = parse("  pdfs ")
    assert pq.kinds == {"pdf"} and pq.filter_only


def test_chip_kinds_and_query_words_are_a_union() -> None:
    pq = parse("slide about gradient descent", kinds={"pdf"})
    assert pq.kinds == {"pdf", "slides"}
    assert parse("gradient descent", kinds=["photo"]).kinds == {"photo"}


def test_whitespace_is_normalised_without_kind_words() -> None:
    assert parse("  error   0x80070005 ") == ParsedQuery(text="error 0x80070005")


def test_chip_labels_cover_every_kind_a_word_maps_to() -> None:
    kinds = set().union(*KIND_WORDS.values())
    assert kinds <= set(KIND_LABELS)
    assert set(KIND_LABELS) == {"screenshot", "photo", "pdf", "doc", "slides"}
