"""M3 done-when with the real CLIP models: a visual query finds a picture with no text, and the gate keeps CLIP
out of an error-code query. OCR and the text embedder stay fake: this is about the CLIP list."""

from pathlib import Path

import pytest

from conftest import camera_exif, draw_checkerboard, draw_sunset_beach, draw_text_image, save_image
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


def test_small_collection_keeps_only_similar_images(conn, cfg, fake_embedder, real_clip, tmp_path) -> None:
    """Fewer than 20 images: no z-score, so a hit needs cosine >= clip_min_cosine (open item r)."""
    root = tmp_path / "few"
    save_image(draw_sunset_beach(), root / "IMG_0101.jpg", exif=camera_exif())
    save_image(draw_text_image((800, 400), [(30, 30, "Settings saved")], 24), root / "Screenshot (1).png")
    (root / "bills.txt").write_text("The electricity invoice is due on the fifth.", encoding="utf-8")
    index_paths(conn, [root], cfg)
    resp = search(conn, "electricity invoice", cfg)
    assert resp.clip["kept"] == 0 and [r.filename for r in resp.results] == ["bills.txt"]
    resp = search(conn, "sunset at the beach", cfg)
    [sunset] = [r for r in resp.results if r.filename == "IMG_0101.jpg"]
    assert not resp.clip["gated"] and resp.clip["kept"] == 1 and sunset.ranks["clip"] == 1
    # bills.txt is still dense #1 here: bge scores it 0.44 against "sunset at the beach", above the 0.42 floor


def test_gate_keeps_clip_out_of_an_error_code(conn, cfg, fake_embedder, real_clip, scenes) -> None:
    index_paths(conn, [scenes], cfg)
    resp = search(conn, "error 0x80070005", cfg)
    assert resp.clip["gated"] and resp.clip["kept"] == 0 and resp.clip["weight"] == 0.5
    assert all(r.ranks["clip"] is None for r in resp.results)
