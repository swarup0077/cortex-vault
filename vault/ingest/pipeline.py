"""Incremental, content-addressed indexing (SPEC §6.1).

One `files` row per unique sha256; any number of `paths` point at it. Renames, moves and duplicates
only touch `paths` and the file's one keyword-only `path` chunk: nothing is re-embedded.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from vault import db, models
from vault.config import Config
from vault.ingest.chunk import chunk_extracted, path_chunk_text
from vault.ingest.classify import (IMAGE_EXTS, PHOTO_PROMPTS, SCREENSHOT_PROMPTS, classify, classify_image, clip_kind,
                                   is_final, known_screen_capture, prompt_vectors)
from vault.ingest.discover import FoundFile, sha256_file, walk_roots
from vault.ingest.extract_image import clip_input, extract_image, load_upright, ocr_uses_cls
from vault.ingest.extract_office import extract_docx, extract_pptx, extract_text
from vault.ingest.extract_pdf import extract_pdf
from vault.ingest.meta import effective_date, filename_date, read_image_meta
from vault.ingest import thumbs
from vault.ingest.records import Extracted, SkipFile

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, int, str], None]


@dataclass
class IndexReport:
    run_id: int = 0
    roots: list[str] = field(default_factory=list)
    missing_roots: list[str] = field(default_factory=list)
    seen: int = 0
    unchanged: int = 0  # fast path, or same content after a touch
    added: int = 0  # new path, new content
    linked: int = 0  # new path, content already indexed (rename, move, duplicate)
    updated: int = 0  # known path whose content changed
    deleted: int = 0  # paths removed by the sweep
    files_deleted: int = 0  # contents removed because no path points at them
    errors: list[dict[str, str]] = field(default_factory=list)  # {path, status, error}
    timings: dict[str, float] = field(default_factory=dict)  # seconds per stage
    per_kind: dict[str, dict[str, float]] = field(default_factory=dict)  # kind -> {files, seconds}
    ocr_images: int = 0  # images and PDF pages sent to OCR
    ocr_empty: int = 0  # of those, how many gave no text (blank pages, photos without any)
    ocr_capped: list[str] = field(default_factory=list)  # PDFs that hit the OCR page cap
    backfilled: int = 0  # older files brought up to date: kind, date, thumbnail, CLIP vector (SPEC §19, item i)
    reclassified: int = 0  # images whose kind CLIP decided after they were indexed (stored vector, no OCR)
    reocr: int = 0  # images OCR'd again because their angle-classifier setting changed (SPEC §6.2)
    interrupted: bool = False
    started_at: str = ""
    finished_at: str = ""

    def files_per_sec(self) -> dict[str, float]:
        return {k: v["files"] / v["seconds"] for k, v in self.per_kind.items() if v["seconds"] > 0}

    def to_json(self) -> str:
        return json.dumps({**asdict(self), "files_per_sec": self.files_per_sec()})


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def extract(path: Path, cfg: Config, screen_capture: bool = False, image: Image.Image | None = None) -> Extracted:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_pdf(path, cfg)
    if ext in IMAGE_EXTS:
        return extract_image(path, cfg, screen_capture=screen_capture, image=image)
    if ext == ".docx":
        return extract_docx(path, cfg)
    if ext == ".pptx":
        return extract_pptx(path, cfg)
    return extract_text(path)


class _Indexer:
    def __init__(self, conn: sqlite3.Connection, cfg: Config, embedder: models.TextEmbedder, compact: bool) -> None:
        self.conn = conn
        self.cfg = cfg
        self.embedder = embedder
        self.always_compact = compact
        self.report = IndexReport(started_at=_now())
        self.run_id = 0
        self._prompts: tuple[np.ndarray, np.ndarray] | bool | None = None  # CLIP prompt vectors; False: unavailable

    @contextmanager
    def timed(self, stage: str) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.report.timings[stage] = self.report.timings.get(stage, 0.0) + time.perf_counter() - t0

    # ------------------------------------------------------------ path bookkeeping (call inside a transaction)

    def _upsert_path(self, f: FoundFile, file_id: int) -> int | None:
        """Point `f.path` at `file_id`; return the file it pointed at before, if any."""
        row = self.conn.execute("SELECT file_id FROM paths WHERE path = ?", (str(f.path),)).fetchone()
        self.conn.execute(
            "INSERT INTO paths(path, file_id, root, size_bytes, mtime, seen_run) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET file_id = excluded.file_id, root = excluded.root, "
            "size_bytes = excluded.size_bytes, mtime = excluded.mtime, seen_run = excluded.seen_run",
            (str(f.path), file_id, str(f.root), f.size, f.mtime, self.run_id),
        )
        return row[0] if row else None

    def _drop_if_orphan(self, file_id: int) -> None:
        """Delete a file with all its rows once no path points at it."""
        if self.conn.execute("SELECT 1 FROM paths WHERE file_id = ? LIMIT 1", (file_id,)).fetchone() is None:
            db.delete_file(self.conn, file_id)
            self.report.files_deleted += 1

    # ------------------------------------------------------------ stages

    def link(self, f: FoundFile, file_id: int) -> None:
        """A changed or new path whose content is already indexed."""
        with self.timed("link"), db.transaction(self.conn):
            old = self._upsert_path(f, file_id)
            if old is None:
                self.report.linked += 1
            elif old == file_id:
                self.report.unchanged += 1  # touched, same bytes
            else:
                self.report.updated += 1
                self._drop_if_orphan(old)

    def add(self, sha: str, group: list[FoundFile]) -> None:
        """New content: extract, chunk and embed outside the transaction, then write it all in one."""
        t0 = time.perf_counter()
        primary = max(group, key=lambda f: f.mtime)
        is_image = primary.path.suffix.lower() in IMAGE_EXTS
        meta = read_image_meta(primary.path) if is_image else None  # read once: kind, date and GPS
        kind, kind_reason = classify_image(primary.path.name, meta) if is_image else classify(primary.path)
        screen = is_image and known_screen_capture(kind_reason)  # only these skip the angle classifier
        status, error, ex, chunks, img = "ok", None, None, [], None
        try:
            with self.timed("extract"):
                if is_image:  # decoded once, for OCR, the thumbnail and CLIP
                    img = load_upright(primary.path)
                ex = extract(primary.path, self.cfg, screen, img)
                chunks = chunk_extracted(ex, self.cfg)
            self.report.timings["extract"] -= ex.ocr_seconds  # report OCR as its own stage
            self.report.timings["ocr"] = self.report.timings.get("ocr", 0.0) + ex.ocr_seconds
            self.report.ocr_images += ex.ocr_images
            self.report.ocr_empty += ex.ocr_empty
            if ex.ocr_capped:
                self.report.ocr_capped.append(str(primary.path))
        except SkipFile as e:
            status, error = "skipped", str(e)
        except Exception as e:  # corrupt or undecodable: record it and keep going
            status, error = "error", f"{type(e).__name__}: {e}"
        if status != "ok":
            log.warning("%s %s: %s", status, primary.path, error)
            self.report.errors.append({"path": str(primary.path), "status": status, "error": error or ""})

        thumb_path = self.thumbnail(sha, primary.path, chunks[0].text if chunks else "", img) if status == "ok" else None
        clip_vecs = self.clip_embed([img]) if status == "ok" and img is not None else None
        if clip_vecs is not None and not is_final(kind_reason):  # no name, EXIF or PNG rule: CLIP decides
            kind, kind_reason = self.zero_shot(clip_vecs[0], (kind, kind_reason))
        ocr_cls = int(ocr_uses_cls(self.cfg, screen)) if is_image and status == "ok" else None
        path_text = path_chunk_text(f.path for f in group)
        with self.timed("embed"):  # the path chunk is keyword-only: no vector (SPEC §5)
            vecs = self.embedder.embed_documents([c.text for c in chunks])

        date, date_source = effective_date(primary.path.name, meta.taken if meta else None,
                                           ex.doc_date if ex else None, primary.mtime)
        lat, lon = meta.gps if meta and meta.gps else (None, None)

        with self.timed("write"), db.transaction(self.conn):
            file_id = self.conn.execute(
                "INSERT INTO files(sha256, kind, kind_reason, effective_date, date_source, width, height, pages,"
                " gps_lat, gps_lon, thumb_path, ocr_cls, status, error, indexed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sha, kind, kind_reason, date, date_source, ex.width if ex else None, ex.height if ex else None,
                 ex.pages if ex else None, lat, lon, thumb_path, ocr_cls, status, error, _now()),
            ).lastrowid
            if status == "ok":
                db.insert_chunk(self.conn, file_id, None, 0, "path", path_text, None)
                for i, (c, v) in enumerate(zip(chunks, vecs), start=1):
                    chunk_id = db.insert_chunk(self.conn, file_id, c.page, i, c.source, c.text, v)
                    if c.lines:
                        db.insert_ocr_lines(self.conn, chunk_id, c.lines)
                if clip_vecs is not None:
                    db.set_image_vector(self.conn, file_id, clip_vecs[0])
            olds = set()
            for n, f in enumerate(group):
                old = self._upsert_path(f, file_id)
                if old is not None:
                    self.report.updated += 1
                    olds.add(old)
                elif n == 0:
                    self.report.added += 1
                else:
                    self.report.linked += 1
            for old in olds:
                self._drop_if_orphan(old)

        stats = self.report.per_kind.setdefault(kind, {"files": 0, "seconds": 0.0})
        stats["files"] += 1
        stats["seconds"] += time.perf_counter() - t0

    def clip_embed(self, images: list[Image.Image]) -> np.ndarray | None:
        """CLIP vectors for upright images; None if CLIP can't run (e.g. no model offline): the files stay
        searchable by text, and the next run's backfill tries again."""
        with self.timed("clip"):
            try:
                vision = models.clip_vision(self.cfg)
                return vision.embed_images([clip_input(im, self.cfg.clip_side) for im in images])
            except Exception as e:
                log.warning("CLIP failed; %d image(s) wait for the next run: %s", len(images), e)
                return None

    def zero_shot(self, vec: np.ndarray, fallback: tuple[str, str]) -> tuple[str, str]:
        """Screenshot or photo by CLIP (SPEC §6.2 rule 4); `fallback` (the size rules) if CLIP's text half can't
        load, and the next run tries again."""
        if self._prompts is None:
            with self.timed("clip"):
                try:
                    text = models.clip_text(self.cfg)
                    self._prompts = (prompt_vectors(text.embed_text, SCREENSHOT_PROMPTS),
                                     prompt_vectors(text.embed_text, PHOTO_PROMPTS))
                except Exception as e:
                    log.warning("CLIP text model failed; images keep their size-rule kind until the next run: %s", e)
                    self._prompts = False
        if not self._prompts:
            return fallback
        return clip_kind(vec, *self._prompts, self.cfg.clip_screenshot_margin)

    def reclassify(self, stop: threading.Event | None) -> None:
        """Bring image kinds and OCR up to the CLIP rule (SPEC §6.2), touching nothing else.

        1. Images no rule decided (size fallbacks, kinds from before the rule) get their kind from their stored CLIP
           vector: nothing is decoded.
        2. Images whose stored OCR used another angle-classifier setting than their kind reason calls for are OCR'd
           again, one transaction each. A kind change alone needs no OCR: the kind reaches OCR only through that
           setting.
        """
        images = self.conn.execute(
            "SELECT id, kind, kind_reason, ocr_cls FROM files WHERE status = 'ok' AND kind IN ('photo', 'screenshot')"
        ).fetchall()
        undecided = {r["id"]: r for r in images if not is_final(r["kind_reason"])}
        updates = []
        for file_id, blob in self._vectors(list(undecided)):  # only these need their vector read
            r = undecided[file_id]
            kind, reason = self.zero_shot(db.blob_to_vec(blob), (r["kind"], r["kind_reason"]))
            if (kind, reason) != (r["kind"], r["kind_reason"]):
                updates.append((kind, reason, file_id))
        if updates:
            with self.timed("write"), db.transaction(self.conn):
                self.conn.executemany("UPDATE files SET kind = ?, kind_reason = ? WHERE id = ?", updates)
            self.report.reclassified += len(updates)

        reasons = {r["id"]: r["kind_reason"] for r in images} | {file_id: reason for _, reason, file_id in updates}
        stale = [r["id"] for r in images
                 if r["ocr_cls"] != int(ocr_uses_cls(self.cfg, known_screen_capture(reasons[r["id"]])))]
        for file_id in stale:
            if stop is not None and stop.is_set():
                self.report.interrupted = True
                return
            path = self.conn.execute("SELECT path FROM paths WHERE file_id = ? ORDER BY mtime DESC LIMIT 1",
                                     (file_id,)).fetchone()
            if path is not None:
                self.reocr(file_id, Path(path[0]), known_screen_capture(reasons[file_id]))

    def _vectors(self, file_ids: list[int]) -> Iterator[tuple[int, bytes]]:
        """Stored CLIP vectors of `file_ids` (those that have one), in batches."""
        for start in range(0, len(file_ids), 500):
            batch = file_ids[start:start + 500]
            yield from self.conn.execute(
                f"SELECT file_id, vec FROM image_vectors WHERE file_id IN ({','.join('?' * len(batch))})", batch)

    def reocr(self, file_id: int, path: Path, screen: bool) -> None:
        """OCR one indexed image again and replace its content chunks, OCR lines and text vectors."""
        if not path.is_file():  # e.g. under a root that's unplugged right now: a later run retries
            return
        try:
            with self.timed("extract"):
                ex = extract(path, self.cfg, screen, load_upright(path))
                chunks = chunk_extracted(ex, self.cfg)
        except Exception as e:
            log.warning("re-OCR of %s failed: %s", path, e)
            return
        self.report.timings["extract"] -= ex.ocr_seconds
        self.report.timings["ocr"] = self.report.timings.get("ocr", 0.0) + ex.ocr_seconds
        self.report.ocr_images += ex.ocr_images
        self.report.ocr_empty += ex.ocr_empty
        with self.timed("embed"):
            vecs = self.embedder.embed_documents([c.text for c in chunks])
        with self.timed("write"), db.transaction(self.conn):
            db.delete_content_chunks(self.conn, file_id)
            for i, (c, v) in enumerate(zip(chunks, vecs), start=1):
                chunk_id = db.insert_chunk(self.conn, file_id, c.page, i, c.source, c.text, v)
                if c.lines:
                    db.insert_ocr_lines(self.conn, chunk_id, c.lines)
            self.conn.execute("UPDATE files SET ocr_cls = ?, indexed_at = ? WHERE id = ?",
                              (int(ocr_uses_cls(self.cfg, screen)), _now(), file_id))
        self.report.reocr += 1

    def backfill(self, stop: threading.Event | None) -> None:
        """Bring files indexed by older versions up to date without re-extracting their text (open item i).

        Images with an interim kind are reclassified and re-dated from their header, files dated by mtime or
        document metadata pick up a filename date, and missing thumbnails and CLIP vectors are made, a batch of
        `clip_batch` files at a time (one transaction each).
        """
        rows = self.conn.execute(
            "SELECT f.id, f.sha256, f.kind, f.kind_reason, f.date_source, f.thumb_path, "
            "iv.file_id IS NOT NULL AS has_clip, p.path, p.mtime "
            "FROM files f JOIN paths p ON p.path = "
            "(SELECT path FROM paths WHERE file_id = f.id ORDER BY mtime DESC LIMIT 1) "
            "LEFT JOIN image_vectors iv ON iv.file_id = f.id "
            "WHERE f.status = 'ok' AND (f.kind_reason LIKE 'interim:%' OR f.thumb_path IS NULL "
            "OR f.date_source IN ('mtime', 'doc_meta') OR (f.kind IN ('photo', 'screenshot') AND iv.file_id IS NULL))"
        ).fetchall()
        step = max(1, self.cfg.clip_batch)
        for start in range(0, len(rows), step):
            if stop is not None and stop.is_set():
                self.report.interrupted = True
                return
            updates: list[tuple[int, dict]] = []
            images: list[tuple[int, Image.Image]] = []
            for r in rows[start:start + step]:
                cols, img = self._refresh(r)
                if cols:
                    updates.append((r["id"], cols))
                if img is not None:
                    images.append((r["id"], img))
            vecs = self.clip_embed([im for _, im in images]) if images else None
            with self.timed("write"), db.transaction(self.conn):
                for file_id, cols in updates:
                    sets = ", ".join(f"{c} = ?" for c in cols)
                    self.conn.execute(f"UPDATE files SET {sets} WHERE id = ?", [*cols.values(), file_id])
                for (file_id, _), vec in zip(images, vecs if vecs is not None else []):
                    db.set_image_vector(self.conn, file_id, vec)
            done = {f for f, _ in updates} | ({f for f, _ in images} if vecs is not None else set())
            self.report.backfilled += len(done)

    def _refresh(self, r: sqlite3.Row) -> tuple[dict, Image.Image | None]:
        """One backfill row: the columns to update, and the upright image if it still needs a CLIP vector."""
        path = Path(r["path"])
        if not path.is_file():  # e.g. under a root that's unplugged right now
            return {}, None
        cols: dict[str, object] = {}
        if r["kind_reason"].startswith("interim:"):
            meta = read_image_meta(path)
            kind, reason = classify_image(path.name, meta)
            date, source = effective_date(path.name, meta.taken if meta else None, None, r["mtime"])
            lat, lon = meta.gps if meta and meta.gps else (None, None)
            cols.update(kind=kind, kind_reason=reason, effective_date=date, date_source=source, gps_lat=lat, gps_lon=lon)
        elif r["date_source"] in ("mtime", "doc_meta") and (from_name := filename_date(path.name)):
            cols.update(effective_date=from_name, date_source="filename")
        is_image = r["kind"] in ("photo", "screenshot")
        need_thumb, need_clip = r["thumb_path"] is None, is_image and not r["has_clip"]
        img = None
        if is_image and (need_thumb or need_clip):
            try:
                img = load_upright(path)
            except Exception as e:
                log.warning("backfill: can't read %s: %s", path, e)
                return cols, None
        if need_thumb:
            first = self.conn.execute(
                "SELECT text FROM chunks WHERE file_id = ? AND source != 'path' ORDER BY ord LIMIT 1", (r["id"],)
            ).fetchone()
            if thumb_path := self.thumbnail(r["sha256"], path, first[0] if first else "", img):
                cols["thumb_path"] = thumb_path
        return cols, img if need_clip else None

    def thumbnail(self, sha: str, path: Path, title: str, image: Image.Image | None) -> str | None:
        """Write the file's thumbnail; a failure costs the thumbnail, never the file."""
        with self.timed("thumbs"):
            try:
                return thumbs.save(thumbs.make_thumb(path, title, image), self.cfg, sha)
            except Exception as e:
                log.warning("no thumbnail for %s: %s", path, e)
                return None

    def sweep(self, roots: list[Path]) -> None:
        """Delete paths under the walked roots that this run didn't see, then orphaned files."""
        with self.timed("sweep"):
            for root in roots:
                stale = self.conn.execute(
                    "SELECT path, file_id FROM paths WHERE root = ? AND seen_run != ?", (str(root), self.run_id)
                ).fetchall()
                by_file: dict[int, list[str]] = {}
                for r in stale:
                    by_file.setdefault(r["file_id"], []).append(r["path"])
                for file_id, paths in by_file.items():
                    with db.transaction(self.conn):
                        self.conn.executemany("DELETE FROM paths WHERE path = ?", [(p,) for p in paths])
                        self._drop_if_orphan(file_id)
                    self.report.deleted += len(paths)

    def refresh_path_chunks(self) -> None:
        """Rewrite path chunks whose text no longer matches the file's paths (FTS only; they have no vector).

        Runs once at the end of every run, so a rename (new path linked, old one swept) is written once, and a
        crash between the two heals on the next run.
        """
        groups: dict[int, list[Path]] = {}
        for r in self.conn.execute(
            "SELECT c.id, p.path FROM chunks c JOIN paths p ON p.file_id = c.file_id WHERE c.source = 'path'"
        ):
            groups.setdefault(r[0], []).append(Path(r[1]))
        current = dict(self.conn.execute("SELECT id, text FROM chunks WHERE source = 'path'").fetchall())
        stale = [(cid, text) for cid, paths in groups.items() if (text := path_chunk_text(paths)) != current[cid]]
        if not stale:
            return
        with self.timed("write"), db.transaction(self.conn):
            for cid, text in stale:
                db.update_chunk_text(self.conn, cid, text, None)

    def compact(self) -> None:
        with self.timed("compact"):
            try:
                db.compact(self.conn)
            except sqlite3.OperationalError as e:  # e.g. another connection is reading; deletes are zeroed anyway
                log.warning("could not compact the index: %s", e)

    def run(self, roots: Iterable[Path | str], stop: threading.Event | None, progress: ProgressFn | None) -> IndexReport:
        rep = self.report
        db.check_text_model(self.conn, self.embedder.name, claim=True)
        db.check_model(self.conn, "clip_model", models.clip_vision_name(self.cfg), claim=True)
        live_roots = []
        for r in roots:
            p = Path(r).resolve()
            if p.is_dir():
                live_roots.append(p)
            else:  # an unplugged drive must not sweep away its index
                log.warning("root not found, skipping (and not sweeping): %s", p)
                rep.missing_roots.append(str(p))
        rep.roots = [str(r) for r in live_roots]
        db.add_roots(self.conn, rep.roots)
        self.run_id = rep.run_id = self.conn.execute(
            "INSERT INTO runs(started_at) VALUES (?)", (rep.started_at,)
        ).lastrowid

        with self.timed("discover"):
            found = walk_roots(live_roots)
        rep.seen = len(found)
        known = {
            r["path"]: (r["size_bytes"], r["mtime"])
            for r in self.conn.execute("SELECT path, size_bytes, mtime FROM paths")
        }
        # Every known path we can see is marked seen, so the sweep only removes what's really gone.
        with db.transaction(self.conn):
            self.conn.executemany(
                "UPDATE paths SET seen_run = ?, root = ? WHERE path = ?",
                [(self.run_id, str(f.root), str(f.path)) for f in found if str(f.path) in known],
            )
        changed = [f for f in found if known.get(str(f.path)) != (f.size, f.mtime)]
        rep.unchanged += len(found) - len(changed)

        pending: dict[str, list[FoundFile]] = {}
        for f in changed:
            if stop is not None and stop.is_set():
                rep.interrupted = True
                break
            try:
                with self.timed("hash"):
                    sha = sha256_file(f.path)
            except OSError as e:
                rep.errors.append({"path": str(f.path), "status": "error", "error": f"cannot read: {e}"})
                continue
            row = self.conn.execute("SELECT id FROM files WHERE sha256 = ?", (sha,)).fetchone()
            if row is not None:
                self.link(f, row[0])
            else:
                pending.setdefault(sha, []).append(f)

        queue = sorted(pending.items(), key=lambda kv: max(f.mtime for f in kv[1]), reverse=True)
        for i, (sha, group) in enumerate(queue):
            if stop is not None and stop.is_set():
                rep.interrupted = True
                break
            if progress:
                progress(i, len(queue), str(group[0].path))
            self.add(sha, group)
        if progress and not rep.interrupted:
            progress(len(queue), len(queue), "")

        if not rep.interrupted:  # unprocessed changes would look like deletions
            self.sweep(live_roots)
        self.refresh_path_chunks()
        if not rep.interrupted:
            self.backfill(stop)
        if not rep.interrupted:
            self.reclassify(stop)
        with self.timed("thumbs"):
            thumbs.sweep(self.conn, self.cfg)  # thumbnails of content that left the index
        if self.always_compact or rep.files_deleted:
            self.compact()

        rep.finished_at = _now()
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, report_json = ? WHERE id = ?",
            (rep.finished_at, rep.to_json(), self.run_id),
        )
        db.set_meta(self.conn, "last_indexed", rep.finished_at)
        log.info(
            "indexed: %d added, %d linked, %d updated, %d deleted, %d unchanged, %d errors, %d OCR'd (%d no text), "
            "%d backfilled, %d reclassified, %d re-OCR'd%s",
            rep.added, rep.linked, rep.updated, rep.deleted, rep.unchanged, len(rep.errors), rep.ocr_images,
            rep.ocr_empty, rep.backfilled, rep.reclassified, rep.reocr,
            " (interrupted)" if rep.interrupted else "",
        )
        return rep


def index_paths(
    conn: sqlite3.Connection,
    roots: Iterable[Path | str],
    cfg: Config | None = None,
    *,
    embedder: models.TextEmbedder | None = None,
    stop: threading.Event | None = None,
    progress: ProgressFn | None = None,
    compact: bool = False,
) -> IndexReport:
    """Incrementally index `roots`. Set `stop` to finish the current file and return early.

    A run that deleted files ends by compacting the DB (FTS merge + VACUUM); `compact=True` always does.
    """
    cfg = cfg or Config()
    return _Indexer(conn, cfg, embedder or models.text_embedder(cfg), compact).run(roots, stop, progress)


def reindex(
    conn: sqlite3.Connection,
    cfg: Config | None = None,
    *,
    embedder: models.TextEmbedder | None = None,
    stop: threading.Event | None = None,
    progress: ProgressFn | None = None,
) -> IndexReport:
    """Full rebuild of every known root, e.g. after a model change. Ends by compacting the DB, so nothing of
    the old index is left in the file."""
    roots = db.get_roots(conn)
    with db.transaction(conn):
        db.wipe_index(conn)
    return index_paths(conn, roots, cfg, embedder=embedder, stop=stop, progress=progress, compact=True)
