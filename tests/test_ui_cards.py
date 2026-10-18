"""Search-tab HTML (vault/ui/cards.py): escaping, images by id only, 20 cards then "Show more", the weaker
divider and the Visual matches row."""

import re

from vault.library import Detail
from vault.search.engine import SearchResponse, SearchResult
from vault.search.parse import ParsedQuery
from vault.ui import cards


def result(i: int, **kw) -> SearchResult:
    base = dict(file_id=i, kind="screenshot", filename=f"shot {i}.png", folder="C:/shots", paths=[f"C:/shots/shot {i}.png"],
                copies=0, effective_date="2026-10-12T10:15:30", page=None, chunk_id=100 + i,
                snippet_html="access <mark>denied</mark>", ranks={"bm25": i}, rrf_score=1 / (60 + i),
                thumb_url=f"thumbs/{'ab' * 32}.webp")
    return SearchResult(**{**base, **kw})


def response(results, weaker=(), visual=(), kinds=frozenset()) -> SearchResponse:
    return SearchResponse("q", ParsedQuery("q", kinds=kinds), list(results), {"bm25": 3.0}, weaker=list(weaker),
                          visual=list(visual))


def same(path: str) -> str:
    return path


def test_card_escapes_everything_from_the_index() -> None:
    r = result(1, filename='<img src=x onerror=alert(1)>.png', paths=['C:/a"b/<x>.png'], badges=["visual match"])
    html = cards.card(r, 1, None, same)
    assert "<img src=x" not in html and "&lt;img src=x onerror=alert(1)&gt;.png" in html
    assert 'a&quot;b' in html
    assert "<mark>denied</mark>" in html  # the snippet keeps its marks


def test_images_are_referenced_by_id_with_the_session() -> None:
    html = cards.card(result(7), 1, "1b4e28ba-2fa1-11d2-883f-0016d3cca427", same)
    src = re.search(r'src="([^"]+)"', html).group(1)
    assert src.startswith("/thumb/7?") and "s=1b4e28ba-2fa1-11d2-883f-0016d3cca427" in src
    assert "shots" not in src and "thumbs/" not in src
    assert cards.image_url("preview", 7, None, page=3) == "/preview/7?page=3"


def test_twenty_cards_then_show_more_up_to_fifty() -> None:
    html = cards.results_html(response([result(i) for i in range(1, 46)], weaker=[
        result(i, weaker=True, badges=["weaker match"]) for i in range(46, 51)]), None, same, page=20)
    first, more = html.split('<div class="v-list v-more" hidden>')
    assert first.count('class="v-card"') == 20 and more.count('class="v-card"') == 30
    assert "Show more (30)" in html
    assert more.count("Weaker matches") == 1 and first.count("Weaker matches") == 0


def test_weaker_divider_comes_before_the_first_weaker_match() -> None:
    html = cards.results_html(response([result(1)], weaker=[result(2, weaker=True)]), None, same, page=20)
    assert html.index("Weaker matches") > html.index("shot 1.png") and html.index("Weaker matches") < html.index(
        "shot 2.png")
    assert "Show more" not in html and "1 result · 1 weaker" in html


def test_visual_row_only_when_clip_kept_something() -> None:
    assert "Visual matches" not in cards.results_html(response([result(1)]), None, same, page=20)
    html = cards.results_html(response([result(1)], visual=[result(2, kind="photo")]), None, same, page=20)
    assert "Visual matches" in html and 'class="v-tile" data-file="2"' in html


def test_page_or_slide_and_copies_on_the_card() -> None:
    assert "slide 3" in cards.card(result(1, kind="slides", page=3), 1, None, same)
    assert "page 2" in cards.card(result(1, kind="pdf", page=2), 1, None, same)
    assert "+2 copies" in cards.card(result(1, copies=2), 1, None, same)


def test_empty_results_name_the_filter() -> None:
    html = cards.results_html(response([], kinds=frozenset({"pdf"})), None, same, page=20)
    assert "No results among PDFs" in html


def test_detail_shows_the_chunk_and_every_path_and_the_matched_page() -> None:
    d = Detail(file_id=4, kind="pdf", filename="notes.pdf", paths=["corpus/a/notes.pdf", "corpus/b/notes.pdf"],
               effective_date="2026-09-10T10:00:00", page=3, pages=4, chunk_html="the <mark>SVD</mark>")
    html = cards.detail_html(d, None, page=3)
    assert 'src="/preview/4?page=3"' in html and "Page 3 of 4" in html and "<mark>SVD</mark>" in html
    assert html.count("<li>") == 2 and "Locations" in html


def test_nice_date() -> None:
    assert cards.nice_date("2026-10-02T08:45:00") == "2 Oct 2026"
    assert cards.nice_date(None) == ""
