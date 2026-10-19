"""End-to-end search. Fast tests use the fake embedder; `slow` ones load bge-small (the M1 criteria)."""

import sqlite3
from collections.abc import Iterator

import pytest

from vault import db, models
from vault.config import Config
from vault.ingest.pipeline import index_paths
from vault.search.engine import search


def top(resp):
    return resp.results[0]


def test_bm25_finds_pdf_page(conn, cfg, fake_embedder, tmp_path, make_search_corpus) -> None:
    index_paths(conn, [make_search_corpus(tmp_path / "c")], cfg)
    r = top(search(conn, "singular value decomposition", cfg))
    assert (r.filename, r.page, r.kind) == ("linear algebra notes.pdf", 2, "pdf")
    assert "<mark>singular</mark> <mark>value</mark> <mark>decomposition</mark>" in r.snippet_html
    assert r.ranks["bm25"] == 1


def test_result_lists_copies(conn, cfg, fake_embedder, tmp_path, make_search_corpus) -> None:
    root = make_search_corpus(tmp_path / "c")
    (root / "backup").mkdir()
    (root / "backup" / "recipe.txt").write_bytes((root / "home" / "recipe.txt").read_bytes())
    index_paths(conn, [root], cfg)
    r = top(search(conn, "sourdough starter", cfg))
    assert r.copies == 1 and len(r.paths) == 2 and r.filename == "recipe.txt"


def test_errors_never_returned(conn, cfg, fake_embedder, tmp_path) -> None:
    root = tmp_path / "c"
    root.mkdir()
    (root / "broken.pdf").write_bytes(b"nope")
    index_paths(conn, [root], cfg)
    assert search(conn, "broken", cfg).results == []
    assert search(conn, "", cfg).results == []  # the filter-only path excludes errors too


def test_filter_only_query_sorts_newest_first(conn, cfg, fake_embedder, tmp_path, make_search_corpus) -> None:
    index_paths(conn, [make_search_corpus(tmp_path / "c")], cfg)
    resp = search(conn, "   ", cfg)
    dates = [r.effective_date for r in resp.results]
    assert len(dates) == 6 and dates == sorted(dates, reverse=True)
    assert "filter" in resp.timings_ms


def test_kind_words_and_chips_filter_results(conn, cfg, fake_embedder, tmp_path, make_search_corpus) -> None:
    index_paths(conn, [make_search_corpus(tmp_path / "c")], cfg)
    assert {r.kind for r in search(conn, "pdf about matrices", cfg).results} == {"pdf"}
    resp = search(conn, "pdfs", cfg)  # only a kind word: every PDF, newest first
    assert sorted(r.filename for r in resp.results) == ["diffusion.pdf", "linear algebra notes.pdf"]
    assert {r.kind for r in search(conn, "electricity invoice", cfg, kinds={"slides"}).results} <= {"slides"}
    union = search(conn, "slide about gradient descent", cfg, kinds={"doc"}).results
    assert {r.kind for r in union} <= {"slides", "doc"} and union[0].page == 3


def test_search_refuses_other_model(conn, cfg, fake_embedder, tmp_path, make_search_corpus) -> None:
    index_paths(conn, [make_search_corpus(tmp_path / "c")], cfg)

    class Other(type(fake_embedder)):
        name = "another-model"

    with pytest.raises(db.ModelMismatchError):
        search(conn, "matrix", cfg, embedder=Other())


# ---------------------------------------------------------------- real model (M1 done-when)


@pytest.fixture(scope="module")
def bge_index(tmp_path_factory, make_search_corpus) -> Iterator[tuple[sqlite3.Connection, Config]]:
    tmp = tmp_path_factory.mktemp("bge")
    cfg = Config(data_dir=tmp / "data")
    models.set_text_embedder(None)
    conn = db.connect(cfg.db_path)
    index_paths(conn, [make_search_corpus(tmp / "corpus")], cfg)
    yield conn, cfg
    conn.close()


@pytest.mark.slow
def test_pdf_returns_right_page(bge_index) -> None:
    conn, cfg = bge_index
    r = top(search(conn, "where is singular value decomposition defined", cfg))
    assert (r.filename, r.page) == ("linear algebra notes.pdf", 2)


@pytest.mark.slow
def test_pptx_returns_right_slide(bge_index) -> None:
    conn, cfg = bge_index
    r = top(search(conn, "slide about gradient descent", cfg))
    assert (r.filename, r.page) == ("ml lecture.pptx", 3)


@pytest.mark.slow
def test_docx_found_by_paraphrase(bge_index) -> None:
    conn, cfg = bge_index
    r = top(search(conn, "power bill payment deadline", cfg))
    assert r.filename == "household.docx"
    assert r.ranks == {"bm25": None, "all_terms": None, "dense": 1, "clip": None}  # no shared words: found by meaning alone


def test_dense_floor_is_inclusive(conn, cfg, fake_embedder, tmp_path) -> None:
    """Dense keeps a chunk exactly at dense_min_cosine and drops one just below it (open item s)."""
    from dataclasses import replace

    root = tmp_path / "c"
    root.mkdir()
    text = "alpha gamma delta epsilon"
    (root / "a.txt").write_text(text, encoding="utf-8")
    index_paths(conn, [root], cfg)
    cos = float(fake_embedder.embed_query(text) @ fake_embedder.embed_query("alpha beta"))
    assert 0 < cos < 1
    at = search(conn, "alpha beta", replace(cfg, dense_min_cosine=cos)).results
    assert at[0].ranks["dense"] == 1
    above = search(conn, "alpha beta", replace(cfg, dense_min_cosine=cos + 1e-6)).results
    assert above[0].ranks["dense"] is None and above[0].ranks["bm25"] == 1  # still found by its word


def test_common_words_dont_outrank_the_screenshot(conn, cfg, fake_embedder, fake_ocr, tmp_path) -> None:
    """Owner's pattern (d): docs full of "where is the" used to beat the screenshot holding the content words,
    because BM25 matched them on the stopwords and dense liked the same words."""
    from conftest import draw_text_image, save_image
    from vault.ingest.records import OcrLine

    root = tmp_path / "c"
    save_image(draw_text_image((800, 200), []), root / "Screenshot (4).png")
    fake_ocr.lines_for = lambda img: [OcrLine("WiFi password: hunter2 router admin page", 0.95, 10, 10, 500, 40)]
    for i, topic in enumerate(["meeting", "room", "bus stop"]):
        (root / f"faq{i}.txt").write_text(f"Where is the {topic}? Where is the {topic} today? It is where it was.",
                                          encoding="utf-8")
    index_paths(conn, [root], cfg)
    results = search(conn, "where is the wifi password", cfg).results
    assert results[0].filename == "Screenshot (4).png" and results[0].ranks["bm25"] == 1
    assert all(r.ranks["bm25"] is None for r in results[1:])  # stopwords alone no longer match


def test_all_terms_list_beats_a_long_loosely_matching_doc(conn, fake_embedder, fake_ocr, tmp_path) -> None:
    """Owner's pattern (a): a long transcript's 30 chunks fill dense's top k, crowding out the short screenshot
    that holds every query word, and BM25 alone can't outvote dense #1 + BM25 #2 in RRF."""
    from dataclasses import replace

    from conftest import build_docx, draw_text_image, save_image
    from vault.ingest.records import OcrLine

    # dense_min_cosine is on bge's scale; the fake embedder scores the transcript ~0.37, so no floor here
    cfg = Config(data_dir=tmp_path / "data", top_k=20, dense_min_cosine=0.0)
    root = tmp_path / "c"
    save_image(draw_text_image((800, 200), []), root / "Screenshot (5).png")
    fake_ocr.lines_for = lambda img: [OcrLine(t, 0.95, 10, 10 + 40 * i, 500, 40 + 40 * i) for i, t in enumerate([
        "Trainer warning at step 4000", "gradient clipping threshold exceeded", "loss nan lr 3e-4 batch 64",
        "File Edit View Run Terminal Help", "epoch 12 of 40 eta 03:12"])]
    build_docx(root / "lecture transcript.docx",
               [f"Part {i}. " + "The gradient moves and the threshold holds; " * 20 for i in range(30)])
    index_paths(conn, [root], cfg)

    query = "gradient clipping threshold"
    without = search(conn, query, replace(cfg, weights={**cfg.weights, "all_terms": 0.0})).results
    assert [r.filename for r in without] == ["lecture transcript.docx", "Screenshot (5).png"]
    assert without[1].ranks == {"bm25": 1, "all_terms": None, "dense": None, "clip": None}

    results = search(conn, query, cfg).results
    assert [r.filename for r in results] == ["Screenshot (5).png", "lecture transcript.docx"]
    assert results[0].ranks["all_terms"] == 1
    assert "<mark>gradient</mark> <mark>clipping</mark> <mark>threshold</mark>" in results[0].snippet_html


def test_path_chunks_are_keyword_only(conn, cfg, fake_embedder, tmp_path) -> None:
    """Owner's pattern (b): a photo with no text used to be a weak dense hit for every query through its
    "folder / filename" vector. Now its name is found by BM25 only."""
    from PIL import Image

    from conftest import save_image
    root = tmp_path / "c"
    save_image(Image.new("RGB", (640, 480), "gray"), root / "Goa trip 2025" / "IMG_2041.jpg")
    (root / "bills.txt").write_text("The electricity invoice is due on the fifth.", encoding="utf-8")
    index_paths(conn, [root], cfg)
    assert [r.filename for r in search(conn, "electricity invoice", cfg).results] == ["bills.txt"]
    [r] = search(conn, "goa trip", cfg).results  # bills.txt shares no word: below the dense floor (open item s)
    assert r.filename == "IMG_2041.jpg" and r.ranks["bm25"] == 1 and r.ranks["dense"] is None
    assert all(r.ranks["bm25"] is None for r in search(conn, "jpg", cfg).results)  # the extension isn't indexed


def test_a_contents_page_never_outranks_its_section_page(conn, cfg, fake_embedder, tmp_path) -> None:
    """Open item n's test: a fixture PDF with a contents page (dot leaders naming every topic) and the section page.
    The section page is shown, and the contents page is out of dense search."""
    from conftest import build_pdf
    from vault.search.retrievers import load_text_index

    build_pdf(tmp_path / "book" / "probability.pdf", [
        "Contents\n1 Counting ........ 1\n2 Conditional probability ........ 9\n3 Bayes' theorem ........ 17\n"
        "4 Random variables ........ 25\n5 Expectation ........ 33",
        "Chapter 3. Bayes' theorem relates the probability of a hypothesis given the evidence to the probability of "
        "the evidence given the hypothesis. A positive test for a rare condition is still likely a false alarm.",
    ])
    index_paths(conn, [tmp_path / "book"], cfg)
    r = search(conn, "Bayes theorem", cfg).results[0]
    assert (r.filename, r.page) == ("probability.pdf", 2)
    toc_chunk = conn.execute("SELECT id FROM chunks WHERE page = 1 AND source = 'pdf_text'").fetchone()[0]
    assert toc_chunk not in set(load_text_index(conn, fake_embedder.dim, cfg.dense_min_word, cfg.toc_min_entries).ids)
    only_toc = search(conn, "random variables expectation counting", cfg).results[0]
    assert only_toc.page == 1 and "…" in only_toc.snippet_html and "........" not in only_toc.snippet_html
