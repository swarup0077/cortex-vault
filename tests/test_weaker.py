"""The floors and the gate never cut the list short (SPEC §7.4 step 4), the Visual matches row (open item t),
and the upload scope filter."""

from pathlib import Path

from PIL import Image

from conftest import COLOURS, camera_exif, save_image
from vault.config import Config
from vault.ingest.pipeline import index_paths
from vault.search.engine import search


def photo(root: Path, name: str, colour: str, shade: int = 0) -> Path:
    rgb = tuple(min(255, c + shade) for c in COLOURS[colour])
    return save_image(Image.new("RGB", (640, 480), rgb), root / name, exif=camera_exif())


def names(results) -> list[str]:
    return [r.filename for r in results]


def small_library(root: Path) -> Path:
    photo(root, "IMG_red.jpg", "red")
    for i, colour in enumerate(["green", "blue", "gray", "yellow"]):
        photo(root, f"IMG_{i}.jpg", colour)
    (root / "bills.txt").write_text("The electricity invoice is due on the fifth.", encoding="utf-8")
    (root / "recipe.txt").write_text("Sourdough: mix flour, water and starter.", encoding="utf-8")
    return root


def test_a_visual_query_keeps_one_strong_match_and_lists_the_rest_below(conn, cfg, fake_embedder, tmp_path) -> None:
    index_paths(conn, [small_library(tmp_path / "c")], cfg)
    resp = search(conn, "red", cfg)
    assert names(resp.results) == ["IMG_red.jpg"]  # fusion: the gate kept one image, the floor no text
    weaker = names(resp.weaker)
    assert len(weaker) == 6 and set(weaker) == {"IMG_0.jpg", "IMG_1.jpg", "IMG_2.jpg", "IMG_3.jpg", "bills.txt",
                                                "recipe.txt"}
    assert all(r.weaker and "weaker match" in r.badges for r in resp.weaker)
    assert not any(r.weaker for r in resp.results)


def test_weaker_matches_never_repeat_a_result_and_respect_the_cap(conn, cfg, fake_embedder, tmp_path) -> None:
    root = small_library(tmp_path / "c")
    for i in range(30):
        photo(root, f"IMG_gray_{i:02}.jpg", "gray", shade=i + 1)
    index_paths(conn, [root], cfg)
    resp = search(conn, "red", cfg, limit=12)
    ids = [r.file_id for r in resp.results + resp.weaker]
    assert len(ids) == 12 and len(set(ids)) == 12
    assert search(conn, "red", cfg).weaker  # the default cap leaves room for them


def test_weaker_matches_keep_the_kind_filter(conn, cfg, fake_embedder, tmp_path) -> None:
    index_paths(conn, [small_library(tmp_path / "c")], cfg)
    resp = search(conn, "red", cfg, kinds={"doc"})
    assert resp.results == [] and set(names(resp.weaker)) == {"bills.txt", "recipe.txt"}
    only_one = search(conn, "sourdough", cfg, kinds={"doc"})
    assert names(only_one.results) == ["recipe.txt"] and names(only_one.weaker) == ["bills.txt"]


def test_weaker_matches_can_be_switched_off(conn, cfg, fake_embedder, tmp_path) -> None:
    index_paths(conn, [small_library(tmp_path / "c")], cfg)
    off = Config(data_dir=cfg.data_dir, weaker_matches=False)
    assert search(conn, "red", off).weaker == []


def test_visual_row_holds_the_gated_clip_hits(conn, cfg, fake_embedder, tmp_path) -> None:
    root = small_library(tmp_path / "c")
    photo(root, "IMG_red2.jpg", "red", shade=3)
    index_paths(conn, [root], cfg)
    resp = search(conn, "red", cfg)
    assert set(names(resp.visual)) == {"IMG_red.jpg", "IMG_red2.jpg"}
    assert all(r.thumb_url for r in resp.visual)
    assert search(conn, "electricity invoice", cfg).visual == []  # the gate kept no image


def test_filter_only_search_has_no_tail_or_row(conn, cfg, fake_embedder, tmp_path) -> None:
    index_paths(conn, [small_library(tmp_path / "c")], cfg)
    resp = search(conn, "photos", cfg)
    assert len(resp.results) == 5 and resp.weaker == [] and resp.visual == []


def test_roots_scope_every_list(conn, cfg, fake_embedder, tmp_path) -> None:
    samples, uploads = tmp_path / "samples", tmp_path / "uploads"
    photo(samples, "IMG_sample_red.jpg", "red")
    (samples / "bills.txt").write_text("The electricity invoice is due on the fifth.", encoding="utf-8")
    photo(uploads, "IMG_upload_red.jpg", "red", shade=5)
    (uploads / "invoice.txt").write_text("Electricity invoice, October.", encoding="utf-8")
    index_paths(conn, [samples, uploads], cfg)
    up = str(uploads.resolve())
    mine = search(conn, "red", cfg, roots=[up])
    assert names(mine.results + mine.weaker) == ["IMG_upload_red.jpg", "invoice.txt"]
    assert names(mine.visual) == ["IMG_upload_red.jpg"]
    assert names(search(conn, "electricity invoice", cfg, roots=[up]).results) == ["invoice.txt"]
    assert set(names(search(conn, "", cfg, roots=[up]).results)) == {"IMG_upload_red.jpg", "invoice.txt"}
    both = search(conn, "electricity invoice", cfg)
    assert set(names(both.results)) == {"bills.txt", "invoice.txt"}
