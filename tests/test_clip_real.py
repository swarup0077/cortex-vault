"""M3 done-when with the real CLIP models: a visual query finds a picture with no text, and the gate keeps CLIP
out of an error-code query. OCR stays fake: this is about the CLIP list. The tests of the visual-query rule also
use the real bge, because the dense floor is on its scale."""

from pathlib import Path

import pytest

from conftest import (camera_exif, draw_checkerboard, draw_desktop_ui, draw_phone_chat, draw_sunset_beach,
                      draw_text_image, save_image)
from vault.ingest.pipeline import index_paths
from vault.search.engine import search

pytestmark = pytest.mark.slow


@pytest.fixture
def scenes(tmp_path: Path) -> Path:
    """2 photos and 22 text screenshots: enough candidates (≥ 20) for the z-score gate to run."""
    root = tmp_path / "pictures"
    save_image(draw_sunset_beach(), root / "IMG_0101.jpg", exif=camera_exif())
    save_image(draw_checkerboard(), root / "IMG_0102.jpg", exif=camera_exif())
    for i in range(22):
        lines = [(30, 30 + 40 * k, f"Settings item {i}-{k} enabled") for k in range(8)]
        save_image(draw_text_image((800, 400), lines, 24), root / f"Screenshot ({i}).png")
    return root


def test_visual_query_finds_the_picture(conn, cfg, fake_embedder, real_clip, scenes) -> None:
    index_paths(conn, [scenes], cfg)
    resp = search(conn, "sunset at the beach", cfg)
    r = resp.results[0]
    assert r.filename == "IMG_0101.jpg" and r.ranks["clip"] == 1 and r.badges == ["visual match"]
    assert resp.clip["gated"] and resp.clip["candidates"] == 24 and resp.clip["kept"] == 1
    assert search(conn, "a chessboard", cfg).results[0].filename == "IMG_0102.jpg"


def test_small_collection_keeps_only_similar_images(conn, cfg, real_clip, tmp_path) -> None:
    """Fewer than 20 images: no z-score, so a hit needs cosine >= clip_min_cosine (open item r)."""
    root = tmp_path / "few"
    save_image(draw_sunset_beach(), root / "IMG_0101.jpg", exif=camera_exif())
    save_image(draw_text_image((800, 400), [(30, 30, "Settings saved")], 24), root / "Screenshot (1).png")
    (root / "bills.txt").write_text("The electricity invoice is due on the fifth.", encoding="utf-8")
    index_paths(conn, [root], cfg)
    resp = search(conn, "electricity invoice", cfg)
    assert resp.clip["kept"] == 0 and [r.filename for r in resp.results] == ["bills.txt"]
    resp = search(conn, "sunset at the beach", cfg)
    sunset, bills = resp.results
    assert sunset.filename == "IMG_0101.jpg" and sunset.ranks["clip"] == 1
    assert not resp.clip["gated"] and resp.clip["kept"] == 1 and resp.clip["strong"] == 1 and resp.clip["visual"]
    # bills.txt is still dense #1 (bge: 0.44, above the 0.42 floor), but the strong visual hit now outranks it
    assert bills.filename == "bills.txt" and bills.ranks["dense"] == 1


def test_visual_query_beats_a_file_sharing_one_word(conn, cfg, real_clip, scenes) -> None:
    """Owner's real-file M3 test, on generated files: a document holding one of the query's words (BM25 #1 and
    dense #1, 2/61) outranked the picture CLIP found (1/61) when no file held every word."""
    from conftest import build_docx

    build_docx(scenes / "travel.docx", ["Train to Goa leaves at 6 am; pack sunscreen and a hat for the beach."])
    build_docx(scenes / "household.docx", ["The electricity invoice from the utility must be paid by the fifth."])
    index_paths(conn, [scenes], cfg)
    resp = search(conn, "sunset at the beach", cfg)
    assert [r.filename for r in resp.results[:2]] == ["IMG_0101.jpg", "travel.docx"]
    assert resp.clip["visual"] and resp.weights["bm25"] == 0.5
    text = search(conn, "electricity invoice", cfg)  # a file holds every word: the rule stays off
    assert text.results[0].filename == "household.docx" and not text.clip["visual"]


def test_a_look_alike_picture_doesnt_turn_a_text_query_visual(conn, cfg, real_clip, tmp_path) -> None:
    """Open item z on generated files: in a bigger library CLIP's z-scores grow, so a phone chat that only looks like
    the topic of a text query cleared the strong-outlier bar, the visual-query rule halved BM25, and the chat beat
    the document that answers the query. Its lead over the next chat is tiny; a real visual match's isn't."""
    from dataclasses import replace

    from conftest import build_docx, draw_label_photo

    root = tmp_path / "library"
    save_image(draw_sunset_beach(), root / "IMG_0101.jpg", exif=camera_exif())
    save_image(draw_checkerboard(), root / "IMG_0102.jpg", exif=camera_exif())
    for i in range(90):
        cover = draw_label_photo((640, 480), (160, 120, 480, 360), [(190, 210, f"NOTE {i}", 40)], seed=i)
        save_image(cover, root / f"cover_{i}.png")
    for i in range(30):
        lines = [(30, 30 + 40 * k, f"Settings item {i}-{k} enabled") for k in range(8)]
        save_image(draw_text_image((800, 400), lines, 24), root / f"Screenshot ({i}).png")
    for i in range(6):
        save_image(draw_phone_chat(i), root / f"chat_{i}.png")
    build_docx(root / "office.docx", ["The weekly team sync moves to Thursday because of the holiday."])
    index_paths(conn, [root], cfg)

    query = "team meeting postponed"  # no file holds every word: the rule may fire
    before = search(conn, query, replace(cfg, clip_strong_gap=0.0))  # the z bar alone, as before item z
    assert before.clip["visual"] and before.results[0].filename.startswith("chat_")
    resp = search(conn, query, cfg)
    assert resp.clip["kept"] >= 1 and resp.clip["strong"] == 0 and not resp.clip["visual"]
    assert resp.results[0].filename == "office.docx"
    sunset = search(conn, "sunset at the beach", cfg)  # a real visual match still leads by far
    assert sunset.clip["visual"] and sunset.results[0].filename == "IMG_0101.jpg"


def test_gate_keeps_clip_out_of_an_error_code(conn, cfg, fake_embedder, real_clip, scenes) -> None:
    index_paths(conn, [scenes], cfg)
    resp = search(conn, "error 0x80070005", cfg)
    assert resp.clip["gated"] and resp.clip["kept"] == 0 and resp.clip["weight"] == 0.5
    assert all(r.ranks["clip"] is None for r in resp.results)


def test_clip_tells_forwarded_screens_from_photos(conn, cfg, fake_embedder, fake_ocr, real_clip, tmp_path) -> None:
    """SPEC §6.2 rule 4: WhatsApp-style JPEGs with no EXIF at screen sizes, which the size rule called screenshots.
    CLIP tells the app window from the photo, and every one keeps the angle classifier."""
    root = tmp_path / "WhatsApp Images"
    for i, (draw, size) in enumerate([(draw_sunset_beach, (1600, 900)), (draw_sunset_beach, (720, 1600)),
                                      (draw_desktop_ui, (1600, 900)), (draw_desktop_ui, (720, 1600))], start=1):
        save_image(draw(*size), root / f"IMG-20250312-WA000{i}.jpg")
    index_paths(conn, [root], cfg)
    got = dict(conn.execute("SELECT f.width || 'x' || f.height || ' ' || substr(p.path, -8), f.kind_reason "
                            "FROM files f JOIN paths p ON p.file_id = f.id").fetchall())
    assert got == {"1600x900 0001.jpg": "clip:photo", "720x1600 0002.jpg": "clip:photo",
                   "1600x900 0003.jpg": "clip:screenshot", "720x1600 0004.jpg": "clip:screenshot"}
    assert fake_ocr.use_cls == [None] * 4 and {r[0] for r in conn.execute("SELECT ocr_cls FROM files")} == {1}
