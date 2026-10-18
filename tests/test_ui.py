"""The app's handlers and its id-only image routes (vault/ui/layout.py, vault/ui/server.py), without a browser."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from conftest import camera_exif, save_image
from vault.config import Config
from vault.library import Library
from vault.ui import layout
from vault.ui.layout import UiContext, build_blocks
from vault.ui.server import make_app


@pytest.fixture
def ctx(tmp_path, fake_embedder, make_search_corpus) -> UiContext:
    root = make_search_corpus(tmp_path / "files")
    save_image(Image.new("RGB", (640, 480), (220, 30, 30)), root / "photos" / "IMG_red.jpg", exif=camera_exif())
    lib = Library(Config(data_dir=tmp_path / "data"))
    lib.index([root])
    return UiContext("local", lib, examples=layout.LOCAL_EXAMPLES)


def test_search_handler_renders_cards_and_returns_the_parsed_text(ctx) -> None:
    out, used = layout.run_search(ctx, "  slide about   gradient descent ", [], None, False, None)
    assert "ml lecture.pptx" in out
    assert "slide 3" in out and used == "about gradient descent"
    empty, used = layout.run_search(ctx, "", [], None, False, None)
    assert "v-empty" in empty and used == ""


def test_chips_filter_and_scores_show(ctx) -> None:
    out, _ = layout.run_search(ctx, "electricity invoice", ["pdf"], None, True, None)
    assert "household.docx" not in out and "rrf" in out


def test_detail_from_a_clicked_id(ctx) -> None:
    hit = ctx.library.search("singular value decomposition").results[0]
    body, visible, fid = layout.show_detail(ctx, "singular value decomposition", None, None, hit.file_id,
                                            hit.chunk_id, hit.page)
    assert visible and fid == hit.file_id and "<mark>singular</mark>" in body and "/preview/" in body
    assert layout.show_detail(ctx, "x", None, None, "not a number", None, None) == ("", False, None)
    assert layout.show_detail(ctx, "x", None, None, 999_999, None, None) == ("", False, None)


def test_open_file_is_local_only_and_by_id(ctx, monkeypatch) -> None:
    opened = []
    monkeypatch.setattr(layout.platform, "open_file", lambda p: opened.append(p))
    hit = ctx.library.search("sourdough").results[0]
    assert layout.open_file(ctx, hit.file_id).startswith("Opened recipe.txt") and opened[0].name == "recipe.txt"
    demo = UiContext("demo", ctx.library)
    assert layout.open_file(demo, hit.file_id) == "Nothing to open." and len(opened) == 1


def test_blocks_build_in_both_modes(ctx) -> None:
    for mode in ("local", "demo"):
        blocks = build_blocks(UiContext(mode, ctx.library, examples=["a"]))
        assert blocks is not None


def test_image_routes_serve_by_id_only(ctx) -> None:
    app = make_app(build_blocks(ctx), lambda s: ctx.library if s is None else None)
    client = TestClient(app)
    hit = ctx.library.search("red").results[0]
    r = client.get(f"/thumb/{hit.file_id}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert client.get(f"/preview/{hit.file_id}").status_code == 200
    pdf = ctx.library.search("singular value decomposition").results[0]
    assert client.get(f"/preview/{pdf.file_id}?page=2").status_code == 200
    assert client.get("/thumb/999999").status_code == 404
    assert client.get(f"/thumb/{hit.file_id}?s=someone-else").status_code == 404
    assert client.get("/thumb/..%2F..%2Fvault.db").status_code in (404, 422)
    assert client.get("/thumb/1;rm").status_code == 422
