import os
import shutil
import threading
from pathlib import Path

import pytest

from vault import db
from vault.ingest import pipeline
from vault.ingest.pipeline import index_paths, reindex
from vault.search.engine import search

TABLES = ("files", "paths", "chunks", "chunks_fts", "text_vectors")


def counts(conn) -> dict[str, int]:
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}


def content_chunks(conn) -> list[tuple]:
    """(chunk id, text, vector) for every non-path chunk."""
    return conn.execute(
        "SELECT c.id, c.text, v.vec FROM chunks c JOIN text_vectors v ON v.chunk_id = c.id "
        "WHERE c.source != 'path' ORDER BY c.id"
    ).fetchall()


def fts_hits(conn, term: str) -> int:
    return conn.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?", (term,)).fetchone()[0]


def assert_consistent(conn) -> None:
    """Every chunk has exactly one FTS row; every content chunk of a ready file one vector, and a listed file's
    none yet (phase 2 writes them all at once); path chunks never; nothing dangles."""
    c = counts(conn)
    ready = conn.execute("SELECT count(*) FROM chunks c JOIN files f ON f.id = c.file_id "
                         "WHERE c.source != 'path' AND f.stage = 'ready'").fetchone()[0]
    assert c["chunks"] == c["chunks_fts"] and c["text_vectors"] == ready
    assert conn.execute("SELECT count(*) FROM text_vectors v JOIN chunks c ON c.id = v.chunk_id "
                        "JOIN files f ON f.id = c.file_id WHERE f.stage = 'listed'").fetchone()[0] == 0
    assert conn.execute(
        "SELECT count(*) FROM text_vectors v JOIN chunks c ON c.id = v.chunk_id WHERE c.source = 'path'"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM chunks_fts WHERE rowid NOT IN (SELECT id FROM chunks)").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM files WHERE id NOT IN (SELECT file_id FROM paths)").fetchone()[0] == 0


@pytest.fixture
def corpus(tmp_path: Path, make_pdf, make_docx, make_pptx) -> Path:
    root = tmp_path / "My Files"
    make_pdf(root / "papers" / "linear algebra.pdf", ["Intro", "Singular value decomposition defined", "Exercises"])
    make_docx(root / "notes" / "bills.docx", ["The electricity invoice is due on the fifth."])
    make_pptx(root / "decks" / "ml.pptx", [("Welcome", "agenda", ""), ("Gradient descent", "downhill", "")])
    (root / "notes" / "todo.txt").write_text("buy milk\n\ncall the plumber", encoding="utf-8")
    return root


def test_first_index(conn, cfg, fake_embedder, corpus) -> None:
    rep = index_paths(conn, [corpus], cfg)
    assert (rep.added, rep.linked, rep.updated, rep.deleted, rep.errors) == (4, 0, 0, 0, [])
    c = counts(conn)
    assert c["files"] == c["paths"] == 4
    assert_consistent(conn)
    # PDF: 3 pages + path chunk; pages kept
    pages = conn.execute(
        "SELECT page FROM chunks c JOIN files f ON f.id = c.file_id WHERE f.kind = 'pdf' AND c.source = 'pdf_text'"
    ).fetchall()
    assert sorted(r[0] for r in pages) == [1, 2, 3]
    assert conn.execute("SELECT pages FROM files WHERE kind = 'slides'").fetchone()[0] == 2
    assert fts_hits(conn, "papers") == 1  # folder name via the path chunk
    assert db.get_meta(conn, "text_model") == fake_embedder.name
    assert db.get_roots(conn) == [str(corpus.resolve())]
    run = conn.execute("SELECT finished_at, report_json FROM runs").fetchone()
    assert run[0] and '"added": 4' in run[1]
    assert set(rep.per_kind) == {"pdf", "doc", "slides"}
    assert {"discover", "hash", "extract", "embed", "write"} <= set(rep.timings)


def test_second_run_uses_fast_path(conn, cfg, fake_embedder, corpus, monkeypatch) -> None:
    index_paths(conn, [corpus], cfg)
    fake_embedder.embedded.clear()
    hashed = []
    monkeypatch.setattr(pipeline, "sha256_file", lambda p: hashed.append(p))
    rep = index_paths(conn, [corpus], cfg)
    assert rep.unchanged == 4 and hashed == [] and fake_embedder.embedded == []


def test_copy_adds_path_but_no_chunks(conn, cfg, fake_embedder, corpus) -> None:
    index_paths(conn, [corpus], cfg)
    before, chunks_before = counts(conn), content_chunks(conn)
    fake_embedder.embedded.clear()
    (corpus / "backup copy").mkdir()
    shutil.copy2(corpus / "papers" / "linear algebra.pdf", corpus / "backup copy" / "la.pdf")

    rep = index_paths(conn, [corpus], cfg)
    after = counts(conn)
    assert rep.linked == 1 and rep.added == 0
    assert after["paths"] == before["paths"] + 1
    assert after["files"] == before["files"] and after["chunks"] == before["chunks"]
    assert content_chunks(conn) == chunks_before
    # nothing was embedded; the keyword-only path chunk now lists both copies
    assert fake_embedder.embedded == []
    assert conn.execute("SELECT text FROM chunks WHERE source = 'path' AND text LIKE '%la%'").fetchone()[0] == (
        "backup copy / la\npapers / linear algebra")
    assert_consistent(conn)


def test_rename_embeds_nothing(conn, cfg, fake_embedder, corpus) -> None:
    index_paths(conn, [corpus], cfg)
    before, chunks_before = counts(conn), content_chunks(conn)
    fake_embedder.embedded.clear()
    (corpus / "decks" / "ml.pptx").rename(corpus / "decks" / "machine learning lecture.pptx")

    rep = index_paths(conn, [corpus], cfg)
    assert (rep.linked, rep.deleted, rep.added, rep.files_deleted) == (1, 1, 0, 0)
    assert counts(conn) == before
    assert content_chunks(conn) == chunks_before  # same ids, texts and vectors
    assert fake_embedder.embedded == []  # the path chunk is keyword-only (SPEC §5, §19)
    assert fts_hits(conn, "lecture") == 1 and fts_hits(conn, "ml") == 0
    assert_consistent(conn)


def test_deleting_last_path_removes_all_rows(conn, cfg, fake_embedder, corpus) -> None:
    index_paths(conn, [corpus], cfg)
    pdf_id = conn.execute("SELECT id FROM files WHERE kind = 'pdf'").fetchone()[0]
    chunk_ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE file_id = ?", (pdf_id,))]
    (corpus / "papers" / "linear algebra.pdf").unlink()

    rep = index_paths(conn, [corpus], cfg)
    assert (rep.deleted, rep.files_deleted) == (1, 1)
    assert conn.execute("SELECT count(*) FROM files WHERE id = ?", (pdf_id,)).fetchone()[0] == 0
    marks = ",".join("?" * len(chunk_ids))
    for table, col in (("chunks", "id"), ("chunks_fts", "rowid"), ("text_vectors", "chunk_id")):
        assert conn.execute(f"SELECT count(*) FROM {table} WHERE {col} IN ({marks})", chunk_ids).fetchone()[0] == 0
    assert fts_hits(conn, "singular") == 0
    assert counts(conn)["files"] == 3
    assert_consistent(conn)


def test_deleting_one_copy_keeps_content(conn, cfg, fake_embedder, corpus) -> None:
    (corpus / "dup").mkdir()
    shutil.copy2(corpus / "notes" / "todo.txt", corpus / "dup" / "todo.txt")
    rep = index_paths(conn, [corpus], cfg)
    assert (rep.added, rep.linked) == (4, 1)  # same-run duplicate links to the new content
    (corpus / "notes" / "todo.txt").unlink()
    rep = index_paths(conn, [corpus], cfg)
    assert (rep.deleted, rep.files_deleted) == (1, 0)
    assert fts_hits(conn, "plumber") == 1
    assert conn.execute("SELECT text FROM chunks WHERE source = 'path' AND text LIKE '%todo%'").fetchone()[0] == "dup / todo"
    assert_consistent(conn)


def test_edited_file_repoints_and_drops_old_content(conn, cfg, fake_embedder, corpus) -> None:
    index_paths(conn, [corpus], cfg)
    todo = corpus / "notes" / "todo.txt"
    todo.write_text("renew passport", encoding="utf-8")
    os.utime(todo, (todo.stat().st_atime, todo.stat().st_mtime + 10))
    rep = index_paths(conn, [corpus], cfg)
    assert (rep.updated, rep.files_deleted, rep.added) == (1, 1, 0)
    assert fts_hits(conn, "passport") == 1 and fts_hits(conn, "plumber") == 0
    assert counts(conn)["files"] == 4
    assert_consistent(conn)


def test_touched_file_is_rehashed_but_not_reembedded(conn, cfg, fake_embedder, corpus) -> None:
    index_paths(conn, [corpus], cfg)
    fake_embedder.embedded.clear()
    todo = corpus / "notes" / "todo.txt"
    os.utime(todo, (todo.stat().st_atime, todo.stat().st_mtime + 10))
    rep = index_paths(conn, [corpus], cfg)
    assert rep.unchanged == 4 and fake_embedder.embedded == []
    assert conn.execute("SELECT mtime FROM paths WHERE path = ?", (str(todo.resolve()),)).fetchone()[0] == todo.stat().st_mtime


def test_newest_first(conn, cfg, fake_embedder, tmp_path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    for i, name in enumerate(["old.txt", "newest.txt", "mid.txt"]):
        p = root / name
        p.write_text(f"file {name}", encoding="utf-8")
        os.utime(p, (1_700_000_000, 1_700_000_000 + {"old.txt": 0, "mid.txt": 100, "newest.txt": 200}[name]))
    order = {"list": [], "read": []}
    index_paths(conn, [root], cfg,
                progress=lambda i, n, path, phase="read": path and order[phase].append(Path(path).name))
    assert order["list"] == order["read"] == ["newest.txt", "mid.txt", "old.txt"]  # both phases


def test_errors_are_recorded_and_run_continues(conn, cfg, fake_embedder, tmp_path, make_pdf, make_zip_bomb) -> None:
    root = tmp_path / "r"
    root.mkdir()
    (root / "corrupt.pdf").write_bytes(b"definitely not a pdf")
    make_pdf(root / "locked.pdf", ["secret"], password="pw")
    make_zip_bomb(root / "bomb.docx")
    (root / "good.txt").write_text("hello there", encoding="utf-8")

    rep = index_paths(conn, [root], cfg)
    status = dict(conn.execute("SELECT p.path, f.status FROM files f JOIN paths p ON p.file_id = f.id").fetchall())
    status = {Path(k).name: v for k, v in status.items()}
    assert status == {"corrupt.pdf": "error", "locked.pdf": "skipped", "bomb.docx": "skipped", "good.txt": "ok"}
    assert len(rep.errors) == 3
    assert conn.execute("SELECT count(*) FROM chunks c JOIN files f ON f.id = c.file_id WHERE f.status != 'ok'").fetchone()[0] == 0
    assert fts_hits(conn, "hello") == 1
    assert index_paths(conn, [root], cfg).errors == []  # unchanged failures aren't retried


def test_stop_finishes_current_file_and_skips_sweep(conn, cfg, fake_embedder, corpus) -> None:
    index_paths(conn, [corpus], cfg)
    (corpus / "notes" / "todo.txt").unlink()  # would be swept by a full run
    for i in range(3):
        (corpus / f"new{i}.txt").write_text(f"new file {i}", encoding="utf-8")
    stop = threading.Event()
    rep = index_paths(conn, [corpus], cfg, stop=stop, progress=lambda i, n, p, phase="read": i == 0 and stop.set())
    assert rep.interrupted and rep.added == 1 and rep.deleted == 0
    assert counts(conn)["files"] == 5  # 4 old (todo not swept yet) + 1 new
    rep = index_paths(conn, [corpus], cfg)  # resumes
    assert (rep.added, rep.deleted, rep.unchanged) == (2, 1, 4)
    assert counts(conn)["files"] == 6
    assert_consistent(conn)


def test_crash_mid_file_leaves_no_partial_rows(conn, cfg, fake_embedder, corpus, monkeypatch) -> None:
    real_insert = db.insert_chunk
    calls = {"n": 0}

    def flaky_insert(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 7:  # partway through some file's chunks
            raise KeyboardInterrupt
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(db, "insert_chunk", flaky_insert)
    with pytest.raises(KeyboardInterrupt):
        index_paths(conn, [corpus], cfg)
    assert_consistent(conn)
    partial = counts(conn)["files"]
    assert 0 < partial < 4

    monkeypatch.setattr(db, "insert_chunk", real_insert)
    rep = index_paths(conn, [corpus], cfg)
    assert rep.added == 4 - partial and counts(conn)["files"] == 4
    assert_consistent(conn)


def test_missing_root_is_not_swept(conn, cfg, fake_embedder, corpus, tmp_path) -> None:
    index_paths(conn, [corpus], cfg)
    moved = corpus.rename(tmp_path / "unplugged")
    rep = index_paths(conn, [corpus], cfg)
    assert rep.missing_roots == [str(corpus.resolve())] and rep.deleted == 0
    assert counts(conn)["paths"] == 4
    moved.rename(corpus)


def test_other_roots_are_left_alone(conn, cfg, fake_embedder, corpus, tmp_path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.txt").write_text("other root file", encoding="utf-8")
    index_paths(conn, [corpus, other], cfg)
    rep = index_paths(conn, [other], cfg)
    assert rep.deleted == 0 and counts(conn)["files"] == 5


def test_model_mismatch_refuses(conn, cfg, fake_embedder, corpus) -> None:
    index_paths(conn, [corpus], cfg)

    class Other(type(fake_embedder)):
        name = "another-model"

    with pytest.raises(db.ModelMismatchError):
        index_paths(conn, [corpus], cfg, embedder=Other())
    rep = reindex(conn, cfg, embedder=Other())
    assert rep.added == 4 and db.get_meta(conn, "text_model") == "another-model"
    assert_consistent(conn)


def test_stale_path_chunk_heals_next_run(conn, cfg, fake_embedder, corpus, monkeypatch) -> None:
    index_paths(conn, [corpus], cfg)
    (corpus / "notes" / "todo.txt").rename(corpus / "notes" / "chores.txt")
    monkeypatch.setattr(pipeline._Indexer, "refresh_path_chunks", lambda self: None)  # "crash" before refresh
    index_paths(conn, [corpus], cfg)
    assert fts_hits(conn, "chores") == 0
    monkeypatch.undo()
    fake_embedder.embedded.clear()
    index_paths(conn, [corpus], cfg)
    assert fts_hits(conn, "chores") == 1 and fake_embedder.embedded == []


# ---------------------------------------------------------------- images and OCR (M2), fake OCR engine

from PIL import Image  # noqa: E402

from conftest import draw_text_image, save_image  # noqa: E402
from vault.ingest.records import OcrLine  # noqa: E402


@pytest.fixture
def shots(tmp_path: Path, fake_ocr) -> Path:
    root = tmp_path / "Pictures"
    save_image(draw_text_image((800, 200), []), root / "Screenshot (3).png")
    save_image(Image.new("RGB", (640, 480), "gray"), root / "trip" / "IMG_0001.jpg")
    fake_ocr.lines_for = lambda img: [
        OcrLine("Access is denied", 0.90, 10, 50, 280, 80),
        OcrLine("ERROR Ox80070005", 0.95, 10, 10, 300, 40),
        OcrLine("smudge", 0.20, 10, 120, 80, 140),
    ] if img.shape[1] == 800 else []
    return root


def test_images_are_ocrd_and_lines_stored(conn, cfg, fake_embedder, shots) -> None:
    rep = index_paths(conn, [shots], cfg)
    assert (rep.added, rep.errors, rep.ocr_images, rep.ocr_empty) == (2, [], 2, 1) and "ocr" in rep.timings
    assert_consistent(conn)
    files = {r["kind"]: tuple(r) for r in conn.execute("SELECT kind, kind_reason, width, height FROM files")}
    # no name, EXIF or PNG rule for the photo: CLIP decides (the fake sees grey as a photograph)
    assert files == {"screenshot": ("screenshot", "filename:prtscn", 800, 200), "photo": ("photo", "clip:photo", 640, 480)}

    [chunk] = conn.execute("SELECT id, text, page FROM chunks WHERE source = 'ocr'").fetchall()
    assert (chunk["text"], chunk["page"]) == ("ERROR 0x80070005 Access is denied", None)
    lines = conn.execute(
        "SELECT line_no, block_no, text, conf, x0, y0, x1, y1 FROM ocr_lines WHERE chunk_id = ? ORDER BY line_no",
        (chunk["id"],),
    ).fetchall()
    assert " ".join(r["text"] for r in lines) == chunk["text"]  # corrected text, same as the chunk
    assert tuple(lines[0])[2:] == ("ERROR 0x80070005", 0.95, 10, 10, 300, 40)
    assert fts_hits(conn, '"0x80070005"') == 1
    # the photo with no text is still indexed, with just its path chunk
    assert conn.execute(
        "SELECT group_concat(c.source) FROM chunks c JOIN files f ON f.id = c.file_id WHERE f.kind = 'photo'"
    ).fetchone()[0] == "path"


def test_deleting_an_image_removes_its_ocr_lines(conn, cfg, fake_embedder, shots) -> None:
    index_paths(conn, [shots], cfg)
    assert conn.execute("SELECT count(*) FROM ocr_lines").fetchone()[0] == 2
    (shots / "Screenshot (3).png").unlink()
    index_paths(conn, [shots], cfg)
    assert conn.execute("SELECT count(*) FROM ocr_lines").fetchone()[0] == 0
    assert_consistent(conn)


def test_corrupt_image_is_recorded_and_run_continues(conn, cfg, fake_embedder, shots) -> None:
    (shots / "broken.jpg").write_bytes(b"not a jpeg")
    rep = index_paths(conn, [shots], cfg)
    assert rep.added == 3 and [e["status"] for e in rep.errors] == ["error"]
    assert "broken.jpg" in rep.errors[0]["path"]


def test_screenshots_skip_the_angle_classifier(conn, cfg, fake_embedder, shots, fake_ocr) -> None:
    index_paths(conn, [shots], cfg)
    by_size = dict(zip(fake_ocr.seen, fake_ocr.use_cls))
    assert by_size == {(800, 200): False, (640, 480): None}  # screenshot: off; photo: the configured default


SECRET = b"zqxjvplk"  # unchanged by the porter stemmer, so the FTS index holds these exact bytes


def without_secure_delete(conn) -> None:
    """Make `conn` behave like a DB written before secure delete was on: deletes leave text behind."""
    conn.execute("PRAGMA secure_delete = OFF")
    conn.execute("INSERT INTO chunks_fts(chunks_fts, rank) VALUES('secure-delete', 0)")


def test_run_that_deletes_files_compacts_the_db(conn, cfg, fake_embedder, corpus) -> None:
    without_secure_delete(conn)
    (corpus / "notes" / "secret.txt").write_text("the zqxjvplk account", encoding="utf-8")
    rep = index_paths(conn, [corpus], cfg)
    assert "compact" not in rep.timings  # nothing deleted, nothing to compact
    (corpus / "notes" / "secret.txt").unlink()

    rep = index_paths(conn, [corpus], cfg)
    assert rep.files_deleted == 1 and "compact" in rep.timings
    assert SECRET not in cfg.db_path.read_bytes()
    assert fts_hits(conn, "electricity") == 1  # the rest of the index is intact
    assert_consistent(conn)


def test_reindex_compacts_away_old_leftovers(conn, cfg, fake_embedder, corpus) -> None:
    without_secure_delete(conn)
    with db.transaction(conn):  # a file deleted long ago, before secure delete
        fid = conn.execute("INSERT INTO files(sha256, kind) VALUES ('old', 'doc')").lastrowid
        db.insert_chunk(conn, fid, None, 0, "doc_text", "the zqxjvplk account", None)
    index_paths(conn, [corpus], cfg)
    with db.transaction(conn):
        db.delete_file(conn, fid)
    assert SECRET in cfg.db_path.read_bytes()

    rep = reindex(conn, cfg)
    assert "compact" in rep.timings and SECRET not in cfg.db_path.read_bytes()
    assert counts(conn)["files"] == 4
    assert_consistent(conn)


# ---------------------------------------------------------------- screenshot vs photo by CLIP (SPEC §6.2 rule 4)


def kinds(conn) -> dict[str, tuple]:
    return {Path(r[0]).name: tuple(r[1:]) for r in conn.execute(
        "SELECT p.path, f.kind, f.kind_reason, f.ocr_cls FROM files f JOIN paths p ON p.file_id = f.id")}


@pytest.fixture
def forwarded(tmp_path: Path) -> Path:
    """WhatsApp-style files: no camera EXIF, screen-like sizes. The fake CLIP sees grey as a photo, white as a
    screen."""
    root = tmp_path / "WhatsApp Images"
    save_image(Image.new("RGB", (1600, 900), "gray"), root / "IMG-20250312-WA0001.jpg")  # a forwarded photo
    save_image(Image.new("RGB", (720, 1600), "white"), root / "IMG-20250312-WA0002.jpg")  # a forwarded screenshot
    save_image(Image.new("RGB", (800, 200), "white"), root / "diagram.png")
    return root


def test_forwarded_photo_at_a_screen_size_is_a_photo(conn, cfg, fake_embedder, fake_ocr, forwarded) -> None:
    """The size rule called a 1600×900 forwarded photo a screenshot and skipped the angle classifier. Now CLIP
    decides the kind, and only a filename or PNG screenshot skips the classifier."""
    index_paths(conn, [forwarded], cfg)
    assert kinds(conn) == {"IMG-20250312-WA0001.jpg": ("photo", "clip:photo", 1),
                           "IMG-20250312-WA0002.jpg": ("screenshot", "clip:screenshot", 1),
                           "diagram.png": ("screenshot", "png-no-exif", 0)}
    assert dict(zip(fake_ocr.seen, fake_ocr.use_cls)) == {(1600, 900): None, (720, 1600): None, (800, 200): False}


def test_without_clip_the_size_rule_holds_until_the_next_run(conn, cfg, fake_embedder, fake_ocr, fake_clip,
                                                              forwarded, monkeypatch) -> None:
    monkeypatch.setattr(fake_clip, "embed_images", lambda images: (_ for _ in ()).throw(RuntimeError("offline")))
    index_paths(conn, [forwarded], cfg)
    assert kinds(conn)["IMG-20250312-WA0001.jpg"] == ("screenshot", "screen-size:1600x900", 1)  # classifier still on
    monkeypatch.undo()
    ocr_calls = len(fake_ocr.seen)
    rep = index_paths(conn, [forwarded], cfg)
    assert kinds(conn)["IMG-20250312-WA0001.jpg"] == ("photo", "clip:photo", 1)
    assert (rep.reclassified, rep.reocr, len(fake_ocr.seen)) == (2, 0, ocr_calls)  # relabelled, not OCR'd again


def test_older_index_reocrs_only_where_the_angle_setting_changed(conn, cfg, fake_embedder, fake_ocr,
                                                                  forwarded) -> None:
    """An index from before the rule: the 1600×900 photo is a size-rule screenshot OCR'd without the classifier,
    the 720×1600 screen a `default` photo OCR'd with it. Only the first needs OCR again."""
    from vault.ingest.records import OcrLine

    fake_ocr.lines_for = lambda img: [OcrLine("smeared sign" if fake_ocr.use_cls[-1] is False else "OPEN DAILY",
                                              0.9, 10, 10, 200, 40)] if img.shape[1] == 1600 else []
    index_paths(conn, [forwarded], cfg)
    with db.transaction(conn):  # how the code before v3 left them (the migration filled ocr_cls by kind)
        conn.execute("UPDATE files SET kind = 'screenshot', kind_reason = 'screen-size:1600x900', ocr_cls = 0 "
                     "WHERE width = 1600")
        conn.execute("UPDATE files SET kind = 'photo', kind_reason = 'default', ocr_cls = 1 WHERE width = 720")
        conn.execute("UPDATE chunks SET text = 'smeared sign' WHERE source = 'ocr' AND file_id = "
                     "(SELECT id FROM files WHERE width = 1600)")
        conn.execute("UPDATE chunks_fts SET text = 'smeared sign' WHERE rowid = "
                     "(SELECT c.id FROM chunks c JOIN files f ON f.id = c.file_id WHERE f.width = 1600 AND c.source = 'ocr')")
    seen = len(fake_ocr.seen)

    rep = index_paths(conn, [forwarded], cfg)
    assert (rep.reclassified, rep.reocr) == (2, 1)
    assert fake_ocr.seen[seen:] == [(1600, 900)] and fake_ocr.use_cls[seen:] == [None]  # classifier on this time
    assert kinds(conn) == {"IMG-20250312-WA0001.jpg": ("photo", "clip:photo", 1),
                           "IMG-20250312-WA0002.jpg": ("screenshot", "clip:screenshot", 1),
                           "diagram.png": ("screenshot", "png-no-exif", 0)}
    assert search(conn, "open daily", cfg).results[0].filename == "IMG-20250312-WA0001.jpg"
    assert all(r.filename != "IMG-20250312-WA0001.jpg" for r in search(conn, "smeared", cfg).results)
    assert_consistent(conn)

    rep = index_paths(conn, [forwarded], cfg)
    assert (rep.reclassified, rep.reocr, len(fake_ocr.seen)) == (0, 0, seen + 1)  # nothing left to do


def test_remove_root_forgets_its_files_but_keeps_shared_content(conn, cfg, fake_embedder, tmp_path,
                                                                make_search_corpus) -> None:
    from vault.ingest import thumbs
    from vault.ingest.pipeline import remove_root

    a, b = make_search_corpus(tmp_path / "a"), tmp_path / "b"
    b.mkdir()
    (b / "recipe copy.txt").write_bytes((a / "home" / "recipe.txt").read_bytes())
    index_paths(conn, [a, b], cfg)
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 6
    removed = remove_root(conn, str(a.resolve()), cfg)
    assert removed == 5  # the recipe's content is still under b
    assert [r[0] for r in conn.execute("SELECT path FROM paths")] == [str((b / "recipe copy.txt").resolve())]
    assert db.get_roots(conn) == [str(b.resolve())]
    assert conn.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == conn.execute(
        "SELECT count(*) FROM chunks").fetchone()[0]
    assert len(list(thumbs.thumbs_dir(cfg).glob("*.webp"))) == 1


# ---------------------------------------------------------------- two-phase indexing (SPEC §6.1, open item g)


def test_listed_files_are_searchable_by_name_and_native_text_before_phase_two(conn, cfg, fake_embedder, fake_ocr,
                                                                             tmp_path, make_pdf) -> None:
    from conftest import OcrLine, draw_text_image, save_image

    root = tmp_path / "r"
    make_pdf(root / "papers" / "svd notes.pdf", ["Singular value decomposition, defined."])
    save_image(draw_text_image((400, 200), [(20, 80, "ERROR 0x80070005")]), root / "shots" / "dialog box.png")
    fake_ocr.lines_for = lambda img: [OcrLine("ERROR 0x80070005", 0.99, 20, 80, 300, 110)]
    seen = {}

    def listed() -> None:  # between the phases: names and native text are in, OCR and vectors are not
        seen["native"] = fts_hits(conn, "singular")
        seen["name"] = fts_hits(conn, "dialog")
        seen["ocr"] = fts_hits(conn, "0x80070005")
        seen["vectors"] = counts(conn)["text_vectors"]
        seen["stages"] = sorted(r[0] for r in conn.execute("SELECT stage FROM files"))

    rep = index_paths(conn, [root], cfg, listed=listed)
    assert seen == {"native": 1, "name": 1, "ocr": 0, "vectors": 0, "stages": ["listed", "listed"]}
    assert (rep.listed, rep.completed) == (2, 2)
    assert fts_hits(conn, "0x80070005") == 1 and {r[0] for r in conn.execute("SELECT stage FROM files")} == {"ready"}
    assert_consistent(conn)


def test_a_run_stopped_after_listing_is_finished_by_the_next(conn, cfg, fake_embedder, corpus) -> None:
    stop = threading.Event()
    rep = index_paths(conn, [corpus], cfg, stop=stop, listed=stop.set)
    assert rep.interrupted and rep.listed == 4 and rep.completed == 0
    assert {r[0] for r in conn.execute("SELECT stage FROM files")} == {"listed"}
    assert fts_hits(conn, "plumber") == 1 and counts(conn)["text_vectors"] == 0  # keyword-searchable already
    assert_consistent(conn)
    rep = index_paths(conn, [corpus], cfg)
    assert (rep.added, rep.unchanged, rep.completed) == (0, 4, 4)
    assert {r[0] for r in conn.execute("SELECT stage FROM files")} == {"ready"}
    assert_consistent(conn)


def test_an_image_that_fails_ocr_is_an_error_and_a_pdf_keeps_its_text(conn, cfg, fake_embedder, fake_ocr, tmp_path,
                                                                      make_pdf) -> None:
    from conftest import draw_text_image, save_image

    root = tmp_path / "r"
    make_pdf(root / "scan.pdf", ["Native text on page one.", ""])  # page 2 has no text: OCR'd
    save_image(draw_text_image((300, 100), [(10, 40, "hello")]), root / "Screenshot (1).png")

    def broken(img):
        raise RuntimeError("OCR model missing")

    fake_ocr.lines_for = broken
    rep = index_paths(conn, [root], cfg)
    status = {Path(r[0]).name: r[1] for r in conn.execute(
        "SELECT p.path, f.status FROM files f JOIN paths p ON p.file_id = f.id")}
    assert status == {"scan.pdf": "ok", "Screenshot (1).png": "error"} and len(rep.errors) == 1
    assert fts_hits(conn, "native") == 1
    assert_consistent(conn)
