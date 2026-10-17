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
    assert r.ranks == {"bm25": None, "dense": 1}  # no shared words: found by meaning alone
