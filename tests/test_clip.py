"""CLIP as the third list (SPEC §6.5, §7.2–7.4) with the fake CLIP from conftest, plus the backfill of files
indexed before M3 (open item i)."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from conftest import COLOURS, FakeClipText, build_pdf, camera_exif, save_image
from vault import db, models
from vault.ingest import thumbs
from vault.ingest.pipeline import index_paths
from vault.search import retrievers
from vault.search.engine import search
from vault.search.parse import ParsedQuery


def photo(root: Path, name: str, colour: str, size=(640, 480), shade: int = 0) -> Path:
    """A flat-colour camera photo. `shade` nudges the colour so same-colour photos are different files."""
    rgb = tuple(min(255, c + shade) for c in COLOURS[colour])
    return save_image(Image.new("RGB", size, rgb), root / name, exif=camera_exif())


def image_vectors(conn) -> dict[str, np.ndarray]:
    rows = conn.execute("SELECT p.path, v.vec FROM image_vectors v JOIN paths p ON p.file_id = v.file_id").fetchall()
    return {Path(r[0]).name: db.blob_to_vec(r[1]) for r in rows}


# ---------------------------------------------------------------- ingest


def test_every_image_gets_one_clip_vector_but_pdfs_dont(conn, cfg, fake_embedder, fake_clip, tmp_path) -> None:
    root = tmp_path / "c"
    photo(root, "IMG_0001.jpg", "red", size=(4000, 3000))
    save_image(Image.new("RGB", (800, 200), "white"), root / "Screenshot (1).png")
    build_pdf(root / "paper.pdf", ["text"])
    rep = index_paths(conn, [root], cfg)
    assert set(image_vectors(conn)) == {"IMG_0001.jpg", "Screenshot (1).png"} and "clip" in rep.timings
    assert fake_clip.seen == [(597, 448), (800, 200)] or fake_clip.seen == [(800, 200), (597, 448)]  # shrunk first
    assert db.get_meta(conn, "clip_model") == "fake-clip"


def test_clip_failure_keeps_the_file_and_the_next_run_fills_in(conn, cfg, fake_embedder, fake_clip, tmp_path,
                                                                monkeypatch) -> None:
    root = tmp_path / "c"
    photo(root, "IMG_0001.jpg", "red")
    monkeypatch.setattr(fake_clip, "embed_images", lambda images: (_ for _ in ()).throw(RuntimeError("offline")))
    rep = index_paths(conn, [root], cfg)
    assert rep.errors == [] and image_vectors(conn) == {}
    assert conn.execute("SELECT status FROM files").fetchone()[0] == "ok"
    monkeypatch.undo()
    rep = index_paths(conn, [root], cfg)
    assert rep.backfilled == 1 and set(image_vectors(conn)) == {"IMG_0001.jpg"}


# ---------------------------------------------------------------- search


def test_visual_match_has_no_snippet_and_a_badge(conn, cfg, fake_embedder, tmp_path) -> None:
    root = tmp_path / "c"
    for name, colour in [("IMG_1.jpg", "red"), ("IMG_2.jpg", "green"), ("IMG_3.jpg", "blue")]:
        photo(root, name, colour)
    index_paths(conn, [root], cfg)
    resp = search(conn, "red", cfg)
    r = resp.results[0]
    assert r.filename == "IMG_1.jpg" and r.ranks["clip"] == 1 and r.ranks["bm25"] is None
    assert r.badges == ["visual match"] and r.snippet_html == "" and r.thumb_url.startswith("thumbs/")
    # < 20 images: the top 5, but only those at least clip_min_cosine similar (green and blue score 0)
    assert resp.clip == {"candidates": 3, "kept": 1, "strong": 1, "gated": False, "weight": 1.0, "visual": True}


def test_text_free_photo_stays_out_of_a_text_query(conn, cfg, fake_embedder, tmp_path) -> None:
    """Open item r: in a small collection the fallback used to lift any photo into every query."""
    root = tmp_path / "c"
    photo(root, "IMG_2041.jpg", "gray")
    (root / "bills.txt").write_text("The electricity invoice is due on the fifth.", encoding="utf-8")
    index_paths(conn, [root], cfg)
    resp = search(conn, "electricity invoice", cfg)
    assert [r.filename for r in resp.results] == ["bills.txt"]
    assert resp.clip == {"candidates": 1, "kept": 0, "strong": 0, "gated": False, "weight": 1.0, "visual": False}


def test_unrelated_text_file_stays_out_of_a_visual_query(conn, cfg, fake_embedder, tmp_path) -> None:
    """Open item s: dense returned its top k whatever the similarity, so the only text file was dense #1 for any
    query and tied the photo CLIP found. It shares no word with the query: below the dense floor."""
    root = tmp_path / "c"
    photo(root, "IMG_2041.jpg", "red")
    (root / "bills.txt").write_text("The electricity invoice is due on the fifth.", encoding="utf-8")
    index_paths(conn, [root], cfg)
    assert [r.filename for r in search(conn, "red", cfg).results] == ["IMG_2041.jpg"]


def many_images(root: Path, n_gray: int = 24) -> None:
    photo(root, "IMG_red.jpg", "red")
    for i in range(n_gray):
        photo(root, f"IMG_{i:03}.jpg", "gray", shade=i)  # distinct bytes: identical files would be one row


def test_gate_lets_the_outlier_through(conn, cfg, fake_embedder, tmp_path) -> None:
    many_images(tmp_path / "c")
    index_paths(conn, [tmp_path / "c"], cfg)
    resp = search(conn, "red", cfg)
    assert [r.filename for r in resp.results] == ["IMG_red.jpg"]
    assert resp.clip["gated"] and resp.clip["kept"] == 1 and resp.results[0].clip_gated


def test_gate_keeps_clip_out_of_an_error_code_query(conn, cfg, fake_embedder, fake_ocr, tmp_path) -> None:
    from vault.ingest.records import OcrLine

    root = tmp_path / "c"
    many_images(root)
    save_image(Image.new("RGB", (800, 200), "white"), root / "Screenshot (7).png")
    fake_ocr.lines_for = lambda img: [OcrLine("ERROR 0x80070005", 0.95, 10, 10, 300, 40)] if img.shape[1] == 800 else []
    index_paths(conn, [root], cfg)
    resp = search(conn, "0x80070005", cfg)
    assert resp.results[0].filename == "Screenshot (7).png"
    assert resp.clip == {"candidates": 26, "kept": 0, "strong": 0, "gated": True, "weight": 0.5, "visual": False}
    assert all(r.ranks["clip"] is None for r in resp.results)


# ---------------------------------------------------------------- the visual-query rule (SPEC §7.4)


def car_scene(root: Path, doc: str) -> None:
    """A red photo among 24 grey ones (its z is about 4.9, a strong outlier) and one text file."""
    photo(root, "IMG_0001.jpg", "red")  # a neutral name: the path chunk mustn't match the query
    for i in range(24):
        photo(root, f"IMG_{i + 100}.jpg", "gray", shade=i)
    (root / "note.txt").write_text(doc, encoding="utf-8")


def weak_dense(cfg):
    """No dense floor: the fake embedder's cosines aren't on bge's scale, and the rule must hold for a weak dense
    hit that a real floor lets through."""
    from dataclasses import replace

    return replace(cfg, dense_min_cosine=0.0)


def test_strong_visual_match_beats_a_partial_word_match(conn, cfg, fake_embedder, tmp_path) -> None:
    """Owner's real-file M3 test: when no file holds all the query's words, a file matching some of them through
    BM25 and dense (2/61) outranked CLIP's strong #1 (1/61)."""
    cfg = weak_dense(cfg)
    car_scene(tmp_path / "c", "The car was parked outside the station all night.")
    index_paths(conn, [tmp_path / "c"], cfg)
    resp = search(conn, "red car parked outside", cfg)
    top, note = resp.results[0], next(r for r in resp.results if r.filename == "note.txt")
    assert top.filename == "IMG_0001.jpg" and top.ranks["clip"] == 1
    assert note.ranks["bm25"] == 1 and note.ranks["dense"] == 1 and note.ranks["all_terms"] is None
    assert resp.clip["visual"] and resp.clip["strong"] == 1 and resp.weights["bm25"] == 0.5
    assert top.rrf_score == pytest.approx(2 / 61) and note.rrf_score == pytest.approx(0.5 / 61 + 1 / 61)


@pytest.mark.parametrize("query, doc", [
    ("red car parked outside", "The red car was parked outside the station all night."),  # a file holds every word
    ("red car AB12CD parked outside", "The car AB12CD was parked outside the station."),  # code-like: text wins
])  # (quoted phrases halve CLIP too, test_gate.py; the parser doesn't extract them until it's picked, §7.1)
def test_visual_rule_stays_off(conn, cfg, fake_embedder, tmp_path, query, doc) -> None:
    cfg = weak_dense(cfg)
    car_scene(tmp_path / "c", doc)
    index_paths(conn, [tmp_path / "c"], cfg)
    resp = search(conn, query, cfg)
    assert not resp.clip["visual"] and resp.weights["bm25"] == 1.0
    assert resp.results[0].filename == "note.txt"


def test_visual_rule_needs_a_strong_hit(conn, cfg, fake_embedder, tmp_path) -> None:
    from dataclasses import replace

    cfg = replace(weak_dense(cfg), clip_strong_z=5.0)  # the red photo's z is ~4.9: gated, not strong
    car_scene(tmp_path / "c", "The car was parked outside the station all night.")
    index_paths(conn, [tmp_path / "c"], cfg)
    resp = search(conn, "red car parked outside", cfg)
    assert resp.clip["kept"] == 1 and resp.clip["strong"] == 0 and not resp.clip["visual"]
    assert resp.results[0].filename == "note.txt"


def test_visual_rule_boosts_only_the_strong_hits(conn, cfg, fake_embedder, tmp_path, monkeypatch) -> None:
    """A weaker gated hit keeps the normal weight, so a long list of photos can't bury a matching document."""
    from vault.search import engine
    from vault.search.gate import Gated

    root = tmp_path / "c"
    for name in ("IMG_A.jpg", "IMG_B.jpg"):
        photo(root, name, "red", shade=len(name) + (name == "IMG_B.jpg"))
    index_paths(conn, [root], cfg)
    ids = dict(conn.execute("SELECT substr(p.path, -9), p.file_id FROM paths p").fetchall())
    a, b = ids["IMG_A.jpg"], ids["IMG_B.jpg"]
    monkeypatch.setattr(engine, "gate", lambda *args: Gated([(a, 0.31), (b, 0.27)], True, 1.0, 30, frozenset({a})))
    by_name = {r.filename: r for r in search(conn, "red", cfg).results}
    assert by_name["IMG_A.jpg"].rrf_score == pytest.approx(2 / 61)
    assert by_name["IMG_B.jpg"].rrf_score == pytest.approx(1 / 62)


def test_clip_retriever_applies_the_filters_first(conn, cfg, fake_embedder, tmp_path) -> None:
    many_images(tmp_path / "c", n_gray=3)
    index_paths(conn, [tmp_path / "c"], cfg)
    index = retrievers.load_image_index(conn)
    red = conn.execute("SELECT f.id FROM files f JOIN paths p ON p.file_id = f.id WHERE p.path LIKE '%red%'").fetchone()[0]
    ids, scores = retrievers.clip_scores(ParsedQuery(text="red"), FakeClipText(), index, allowed={red})
    assert ids.tolist() == [red] and scores.tolist() == pytest.approx([1.0])
    ids, _ = retrievers.clip_scores(ParsedQuery(text="red"), FakeClipText(), index, allowed=set())
    assert ids.tolist() == []


def test_text_only_index_never_loads_clip(conn, cfg, fake_embedder, tmp_path, monkeypatch) -> None:
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "a.txt").write_text("alpha", encoding="utf-8")
    index_paths(conn, [tmp_path / "c"], cfg)
    monkeypatch.setattr(models, "clip_text", lambda cfg=None: pytest.fail("loaded CLIP for a text-only index"))
    assert search(conn, "alpha", cfg).results[0].filename == "a.txt"


def test_search_refuses_another_clip_model(conn, cfg, fake_embedder, tmp_path) -> None:
    photo(tmp_path / "c", "IMG_1.jpg", "red")
    index_paths(conn, [tmp_path / "c"], cfg)

    class Other(FakeClipText):
        name = "another-clip"

    with pytest.raises(db.ModelMismatchError, match="CLIP"):
        search(conn, "red", cfg, clip=Other())


# ---------------------------------------------------------------- backfill: files indexed before M3 (open item i)


def test_backfill_upgrades_m2_rows_without_ocr_or_text_embedding(conn, cfg, fake_embedder, fake_ocr, fake_clip,
                                                                  tmp_path) -> None:
    from vault.ingest.records import OcrLine

    root = tmp_path / "c"
    exif = camera_exif(taken="2025:03:12 10:15:30", gps=((15, 30, 0), "N", (73, 49, 30), "E"))
    save_image(Image.new("RGB", (640, 480), COLOURS["red"]), root / "IMG_0001.jpg", exif=exif)
    save_image(Image.new("RGB", (800, 200), "white"), root / "Screenshot 2026-10-16 170612.jpg")
    build_pdf(root / "DOC-20250312-WA0004.pdf", ["ticket"], created="D:20200101000000")
    fake_ocr.lines_for = lambda img: [OcrLine("Settings saved", 0.95, 10, 10, 300, 40)] if img.shape[1] == 800 else []
    index_paths(conn, [root], cfg)
    want = conn.execute("SELECT sha256, kind, kind_reason, effective_date, date_source, gps_lat, gps_lon, thumb_path "
                        "FROM files ORDER BY sha256").fetchall()
    # Make the rows look like M2 left them: interim kinds, mtime dates, no thumbnails, no CLIP vectors.
    with db.transaction(conn):
        conn.execute("UPDATE files SET kind_reason = 'interim:ext', kind = 'photo', date_source = 'mtime', "
                     "effective_date = '2000-01-01T00:00:00', gps_lat = NULL, gps_lon = NULL, thumb_path = NULL "
                     "WHERE kind != 'pdf'")
        conn.execute("UPDATE files SET date_source = 'doc_meta', effective_date = '2020-01-01T00:00:00', "
                     "thumb_path = NULL WHERE kind = 'pdf'")
        conn.execute("DELETE FROM image_vectors")
    for p in thumbs.thumbs_dir(cfg).glob("*.webp"):
        p.unlink()
    ocr_calls, fake_embedder.embedded = len(fake_ocr.seen), []

    rep = index_paths(conn, [root], cfg)
    assert rep.unchanged == 3 and rep.backfilled == 3
    assert len(fake_ocr.seen) == ocr_calls and fake_embedder.embedded == []  # no OCR, no text embedding
    got = conn.execute("SELECT sha256, kind, kind_reason, effective_date, date_source, gps_lat, gps_lon, thumb_path "
                       "FROM files ORDER BY sha256").fetchall()
    assert [tuple(r) for r in got] == [tuple(r) for r in want]
    assert len(image_vectors(conn)) == 2 and all((cfg.data_dir / r["thumb_path"]).is_file() for r in got)

    rep = index_paths(conn, [root], cfg)
    assert rep.backfilled == 0  # nothing left to do
