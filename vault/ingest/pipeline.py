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

from vault import db, models
from vault.config import Config
from vault.ingest.chunk import chunk_extracted, path_chunk_text
from vault.ingest.classify import IMAGE_EXTS, classify, classify_image
from vault.ingest.discover import FoundFile, sha256_file, walk_roots
from vault.ingest.extract_image import extract_image
from vault.ingest.extract_office import extract_docx, extract_pptx, extract_text
from vault.ingest.extract_pdf import extract_pdf
from vault.ingest.meta import effective_date, read_image_meta
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
    interrupted: bool = False
    started_at: str = ""
    finished_at: str = ""

    def files_per_sec(self) -> dict[str, float]:
        return {k: v["files"] / v["seconds"] for k, v in self.per_kind.items() if v["seconds"] > 0}

    def to_json(self) -> str:
        return json.dumps({**asdict(self), "files_per_sec": self.files_per_sec()})


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def extract(path: Path, cfg: Config, kind: str | None = None) -> Extracted:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_pdf(path, cfg)
    if ext in IMAGE_EXTS:
        return extract_image(path, cfg, kind=kind)
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
        status, error, ex, chunks = "ok", None, None, []
        try:
            with self.timed("extract"):
                ex = extract(primary.path, self.cfg, kind)
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

        path_text = path_chunk_text(f.path for f in group)
        with self.timed("embed"):  # the path chunk is keyword-only: no vector (SPEC §5)
            vecs = self.embedder.embed_documents([c.text for c in chunks])

        date, date_source = effective_date(primary.path.name, meta.taken if meta else None,
                                           ex.doc_date if ex else None, primary.mtime)
        lat, lon = meta.gps if meta and meta.gps else (None, None)

        with self.timed("write"), db.transaction(self.conn):
            file_id = self.conn.execute(
                "INSERT INTO files(sha256, kind, kind_reason, effective_date, date_source, width, height, pages,"
                " gps_lat, gps_lon, status, error, indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sha, kind, kind_reason, date, date_source, ex.width if ex else None, ex.height if ex else None,
                 ex.pages if ex else None, lat, lon, status, error, _now()),
            ).lastrowid
            if status == "ok":
                db.insert_chunk(self.conn, file_id, None, 0, "path", path_text, None)
                for i, (c, v) in enumerate(zip(chunks, vecs), start=1):
                    chunk_id = db.insert_chunk(self.conn, file_id, c.page, i, c.source, c.text, v)
                    if c.lines:
                        db.insert_ocr_lines(self.conn, chunk_id, c.lines)
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
        if self.always_compact or rep.files_deleted:
            self.compact()

        rep.finished_at = _now()
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, report_json = ? WHERE id = ?",
            (rep.finished_at, rep.to_json(), self.run_id),
        )
        db.set_meta(self.conn, "last_indexed", rep.finished_at)
        log.info(
            "indexed: %d added, %d linked, %d updated, %d deleted, %d unchanged, %d errors, %d OCR'd (%d no text)%s",
            rep.added, rep.linked, rep.updated, rep.deleted, rep.unchanged, len(rep.errors), rep.ocr_images,
            rep.ocr_empty,
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
