"""The demo index (SPEC §2, §5, §10): built from corpus/ with repo-relative paths, refused if any image carries
GPS, and opened read-only."""

from pathlib import Path

import pytest
from PIL import Image

from conftest import build_pdf, camera_exif, save_image
from vault import db
from vault.config import Config
from vault.demo import build_demo, gps_files, strip_gps
from vault.ingest.meta import read_image_meta
from vault.library import Library

GPS = ((15, 29, 30), "N", (73, 49, 40), "E")


def repo(tmp_path: Path) -> Path:
    base = tmp_path / "my repo"
    build_pdf(base / "corpus" / "papers" / "2006.11239.pdf", ["Denoising diffusion models reverse a noising process."])
    (base / "corpus" / "notes").mkdir(parents=True)
    (base / "corpus" / "notes" / "recipe.txt").write_text("Sourdough with a ripe starter.", encoding="utf-8")
    save_image(Image.new("RGB", (640, 480), (220, 30, 30)), base / "corpus" / "photos" / "IMG_red.jpg",
               exif=camera_exif(taken="2026:09:21 18:30:12"))
    (base / "corpus" / "MANIFEST.csv").write_text("path,kind\n", encoding="utf-8")
    return base


def test_build_demo_stores_repo_relative_paths(tmp_path, fake_embedder) -> None:
    base = repo(tmp_path)
    out = base / "demo_index"
    rep = build_demo(base / "corpus", out, Config(data_dir=tmp_path / "unused"), base=base)
    assert rep.added == 3 and rep.errors == []
    conn = db.connect(out / "vault.db", read_only=True)
    assert sorted(r[0] for r in conn.execute("SELECT path FROM paths")) == [
        "corpus/notes/recipe.txt", "corpus/papers/2006.11239.pdf", "corpus/photos/IMG_red.jpg"]
    assert {r[0] for r in conn.execute("SELECT root FROM paths")} == {"corpus"}
    assert db.get_roots(conn) == ["corpus"]
    assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 0  # the build run named laptop paths
    assert all(str(tmp_path) not in r[0] for r in conn.execute("SELECT text FROM chunks"))
    conn.close()
    assert len(list((out / "thumbs").glob("*.webp"))) == 3


def test_demo_index_serves_files_read_only(tmp_path, fake_embedder) -> None:
    base = repo(tmp_path)
    out = base / "demo_index"
    build_demo(base / "corpus", out, Config(data_dir=tmp_path / "unused"), base=base)
    lib = Library(Config(data_dir=out), read_only=True, base_dir=base)
    hit = lib.search("diffusion").results[0]
    assert hit.filename == "2006.11239.pdf" and Path(hit.folder) == Path("corpus/papers")
    assert lib.file_path(hit.file_id) == base / "corpus" / "papers" / "2006.11239.pdf"
    assert lib.thumb(hit.file_id).parent == out / "thumbs"
    with pytest.raises(PermissionError):
        lib.index([base / "corpus"])


def test_rebuild_replaces_the_old_index(tmp_path, fake_embedder) -> None:
    base = repo(tmp_path)
    out = base / "demo_index"
    build_demo(base / "corpus", out, Config(data_dir=tmp_path / "unused"), base=base)
    (base / "corpus" / "notes" / "recipe.txt").unlink()
    build_demo(base / "corpus", out, Config(data_dir=tmp_path / "unused"), base=base)
    conn = db.connect(out / "vault.db", read_only=True)
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 2
    conn.close()
    assert len(list((out / "thumbs").glob("*.webp"))) == 2


def test_build_demo_refuses_gps(tmp_path, fake_embedder) -> None:
    base = repo(tmp_path)
    save_image(Image.new("RGB", (64, 64), "blue"), base / "corpus" / "photos" / "IMG_gps.jpg",
               exif=camera_exif(gps=GPS))
    with pytest.raises(ValueError, match="scrub-exif"):
        build_demo(base / "corpus", base / "demo_index", Config(data_dir=tmp_path / "unused"), base=base)
    assert not (base / "demo_index").exists()
    assert [p.name for p in gps_files(base / "corpus")] == ["IMG_gps.jpg"]


def test_strip_gps_keeps_the_rest_of_the_exif(tmp_path) -> None:
    jpg = save_image(Image.new("RGB", (64, 48), "blue"), tmp_path / "IMG_1.jpg",
                     exif=camera_exif(taken="2025:03:12 10:15:30", gps=GPS))
    png = save_image(Image.new("RGB", (64, 48), "blue"), tmp_path / "shot.png", exif=camera_exif(gps=GPS))
    plain = save_image(Image.new("RGB", (64, 48), "blue"), tmp_path / "plain.jpg")
    assert read_image_meta(jpg).gps is not None
    assert strip_gps(jpg) and strip_gps(png) and not strip_gps(plain)
    meta = read_image_meta(jpg)
    assert meta.gps is None and meta.camera == "TestCam Model 1" and meta.taken == "2025-03-12T10:15:30"
    assert gps_files(tmp_path) == []
    assert Image.open(jpg).size == (64, 48)
