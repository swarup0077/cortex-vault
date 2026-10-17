"""Thumbnails for all five kinds, and their deletion with the file (SPEC §6.3)."""

from pathlib import Path

import pymupdf
from PIL import Image

from conftest import build_docx, build_pdf, build_pptx, camera_exif, draw_text_image, save_image
from vault.ingest import pipeline, thumbs
from vault.ingest.pipeline import index_paths, reindex


def thumbs_by_name(conn, cfg) -> dict[str, Path | None]:
    rows = conn.execute("SELECT p.path, f.thumb_path FROM files f JOIN paths p ON p.file_id = f.id").fetchall()
    return {Path(r[0]).name: (cfg.data_dir / r[1] if r[1] else None) for r in rows}


def stored(cfg) -> set[str]:
    folder = thumbs.thumbs_dir(cfg)
    return {p.name for p in folder.glob("*.webp")} if folder.is_dir() else set()


def build_red_first_page(path: Path) -> Path:
    doc = pymupdf.open()
    doc.new_page().draw_rect(pymupdf.Rect(0, 0, 595, 842), color=(1, 0, 0), fill=(1, 0, 0))
    doc.new_page().insert_text((50, 80), "second page", fontsize=18)
    doc.save(path)
    doc.close()
    return path


def test_every_kind_gets_a_webp_thumbnail(conn, cfg, fake_embedder, tmp_path) -> None:
    root = tmp_path / "c"
    save_image(draw_text_image((800, 200), [(10, 10, "hello")]), root / "Screenshot (1).png")
    save_image(Image.new("RGB", (4000, 3000), "navy"), root / "IMG_0001.jpg", exif=camera_exif())
    build_pdf(root / "paper.pdf", ["Abstract", "Methods"])
    build_docx(root / "notes.docx", ["Lecture notes on linear maps"])
    build_pptx(root / "deck.pptx", [("Gradient descent", "steps downhill", "")])
    (root / "todo.txt").write_text("buy milk", encoding="utf-8")
    (root / "readme.md").write_text("# Project\nnotes", encoding="utf-8")
    rep = index_paths(conn, [root], cfg)
    assert rep.errors == [] and "thumbs" in rep.timings

    got = thumbs_by_name(conn, cfg)
    sizes = {}
    for name, path in got.items():
        assert path is not None and path.is_file(), name
        with Image.open(path) as im:
            assert im.format == "WEBP", name
            sizes[name] = im.size
    assert sizes["Screenshot (1).png"] == (320, 80)
    assert sizes["IMG_0001.jpg"] == (320, 240)
    assert max(sizes["paper.pdf"]) == 320 and sizes["paper.pdf"][1] > sizes["paper.pdf"][0]  # portrait page
    for card in ("notes.docx", "deck.pptx", "todo.txt", "readme.md"):
        assert sizes[card] == thumbs.CARD
    sha = conn.execute("SELECT sha256 FROM files WHERE kind = 'pdf'").fetchone()[0]
    assert got["paper.pdf"] == cfg.data_dir / "thumbs" / f"{sha}.webp"


def test_image_thumbnail_is_upright(conn, cfg, fake_embedder, tmp_path) -> None:
    exif = camera_exif()
    exif[0x0112] = 6  # stored sideways: 600×400 on disk, 400×600 shown
    save_image(Image.new("RGB", (600, 400), "green"), tmp_path / "c" / "IMG_0002.jpg", exif=exif)
    index_paths(conn, [tmp_path / "c"], cfg)
    with Image.open(thumbs_by_name(conn, cfg)["IMG_0002.jpg"]) as im:
        assert im.size == (213, 320)


def test_pdf_thumbnail_is_page_one(conn, cfg, fake_embedder, tmp_path) -> None:
    (tmp_path / "c").mkdir()
    build_red_first_page(tmp_path / "c" / "red.pdf")
    index_paths(conn, [tmp_path / "c"], cfg)
    with Image.open(thumbs_by_name(conn, cfg)["red.pdf"]) as im:
        r, g, b = im.convert("RGB").getpixel((im.width // 2, im.height // 2))
    assert r > 200 and g < 60 and b < 60


def test_card_shows_the_file_type_badge(conn, cfg, fake_embedder, tmp_path) -> None:
    build_docx(tmp_path / "c" / "notes.docx", ["Lecture notes"])
    index_paths(conn, [tmp_path / "c"], cfg)
    with Image.open(thumbs_by_name(conn, cfg)["notes.docx"]) as im:
        r, g, b = im.convert("RGB").getpixel((20, 30))  # inside the badge
    assert b > r + 50  # the blue DOCX badge


def test_thumbnail_leaves_with_its_file(conn, cfg, fake_embedder, tmp_path) -> None:
    root = tmp_path / "c"
    (root / "keep.txt").parent.mkdir(parents=True)
    (root / "keep.txt").write_text("stays", encoding="utf-8")
    (root / "gone.txt").write_text("deleted soon", encoding="utf-8")
    (root / "edited.txt").write_text("first version", encoding="utf-8")
    index_paths(conn, [root], cfg)
    before = thumbs_by_name(conn, cfg)
    assert len(stored(cfg)) == 3

    (root / "gone.txt").unlink()
    (root / "edited.txt").write_text("second version, longer", encoding="utf-8")
    index_paths(conn, [root], cfg)
    after = thumbs_by_name(conn, cfg)
    assert not before["gone.txt"].exists() and not before["edited.txt"].exists()
    assert after["edited.txt"].is_file() and after["keep.txt"] == before["keep.txt"]
    assert stored(cfg) == {after["keep.txt"].name, after["edited.txt"].name}


def test_reindex_keeps_only_current_thumbnails(conn, cfg, fake_embedder, tmp_path) -> None:
    root = tmp_path / "c"
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    index_paths(conn, [root], cfg)
    orphan = thumbs.thumbs_dir(cfg) / ("f" * 64 + ".webp")  # left by a crash before its row was committed
    Image.new("RGB", (8, 8)).save(orphan, "WEBP")
    reindex(conn, cfg)
    assert stored(cfg) == {thumbs_by_name(conn, cfg)["a.txt"].name}


def test_thumbnail_failure_never_fails_the_file(conn, cfg, fake_embedder, tmp_path, monkeypatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("renderer broke")

    monkeypatch.setattr(pipeline.thumbs, "make_thumb", boom)
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "a.txt").write_text("alpha", encoding="utf-8")
    rep = index_paths(conn, [tmp_path / "c"], cfg)
    assert rep.errors == [] and thumbs_by_name(conn, cfg) == {"a.txt": None}
    assert conn.execute("SELECT status FROM files").fetchone()[0] == "ok"
