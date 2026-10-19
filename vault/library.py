"""One index as the app sees it (SPEC §8, open item b).

A `Library` owns a DB file, the data dirs its thumbnails live in, and the vectors search needs, loaded once and
rebuilt after indexing. Files, thumbnails and previews are looked up by DB id; the only paths a caller hands in
are the folders local mode indexes. Each call opens its own connection, so the UI's worker threads never share
one.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image

from vault import db, models
from vault.config import REPO_ROOT, Config
from vault.ingest import pipeline
from vault.ingest.extract_image import load_upright
from vault.ingest.pipeline import IndexReport
from vault.platform import resolve_stored_path
from vault.search import highlight as boxes_of
from vault.search import retrievers
from vault.search.engine import SearchResponse, search
from vault.search.snippet import highlight
from vault.vectors import VectorIndex

log = logging.getLogger(__name__)

# One indexing job at a time in the whole process (local "Index now" and demo uploads alike): OCR already uses
# every CPU, and the OCR engine keeps per-call settings on itself (SPEC §8, §19).
INDEX_LOCK = threading.Lock()

PREVIEW_WIDTH = 1200  # previews are at most this wide; PDF pages are rendered at this width
PREVIEW_MAX_HEIGHT = 8000  # a tall scrolling screenshot keeps its width up to this height

ProgressFn = Callable[[int, int, str], None]

# The Archive's file types (SPEC §8): the kinds, with plain-text docs split out of "doc" by their extension.
TYPES = ("screenshot", "pdf", "text", "photo", "doc", "slides")
TEXT_SUFFIXES = (".txt", ".md")
_IS_TEXT = ("EXISTS (SELECT 1 FROM paths t WHERE t.file_id = f.id AND ("
            + " OR ".join(f"lower(t.path) LIKE '%{s}'" for s in TEXT_SUFFIXES) + "))")
_TYPE_WHERE = {t: f"AND f.kind = '{t}'" for t in TYPES if t not in ("text", "doc")} | {
    "text": f"AND f.kind = 'doc' AND {_IS_TEXT}", "doc": f"AND f.kind = 'doc' AND NOT {_IS_TEXT}"}


@dataclass
class Detail:
    """The detail panel's text part (SPEC §8): the matched chunk in full, highlighted, and every path."""

    file_id: int
    kind: str
    filename: str
    paths: list[str]
    effective_date: str | None
    page: int | None
    pages: int | None
    chunk_html: str


@dataclass
class Recent:
    file_id: int
    name: str
    kind: str
    indexed_at: str | None


@dataclass
class Stats:
    """The Index tab's numbers."""

    counts: dict[str, int]  # kind -> files indexed fine
    errors: list[dict[str, str]]  # {path, status, error}
    roots: list[str]
    last_run: dict | None  # the last run's report
    last_indexed: str | None
    db_bytes: int


class Library:
    def __init__(self, cfg: Config, *, db_path: Path | None = None, read_only: bool = False,
                 thumb_dirs: Sequence[Path] = (), base_dir: Path = REPO_ROOT, display_root: Path | None = None) -> None:
        """`thumb_dirs`: more data dirs to look for thumbnails in (a session copy finds the demo's there).
        `base_dir` resolves repo-relative paths (the demo index, SPEC §5). Paths under `display_root` are shown
        relative to it (a session's uploads as `uploads/…`)."""
        self.cfg = cfg
        self.db_path = db_path or cfg.db_path
        self.read_only = read_only
        self.thumb_dirs = [cfg.data_dir, *thumb_dirs]
        self.base_dir = base_dir
        self.display_root = display_root
        self._lock = threading.Lock()
        self._vectors: tuple[VectorIndex, VectorIndex] | None = None
        self._stale = False  # phase 2 stored new vectors since they were loaded
        self._loaded_at = 0.0

    # ------------------------------------------------------------ connections and vectors

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = db.connect(self.db_path, read_only=self.read_only)
        try:
            yield conn
        finally:
            conn.close()

    def _load_vectors(self, conn: sqlite3.Connection) -> tuple[VectorIndex, VectorIndex]:
        return (retrievers.load_text_index(conn, models.text_embedder(self.cfg).dim),
                retrievers.load_image_index(conn))

    def vectors(self, conn: sqlite3.Connection) -> tuple[VectorIndex, VectorIndex]:
        """The cached vectors; while phase 2 is storing new ones, reloaded at most once a second, so a search
        during indexing finds files by meaning as they're read."""
        with self._lock:
            if self._vectors is None or (self._stale and time.monotonic() - self._loaded_at > 1.0):
                self._vectors = self._load_vectors(conn)
                self._stale = False
                self._loaded_at = time.monotonic()
            return self._vectors

    def refresh(self) -> None:
        """Reload the vectors after the index changed."""
        with self.connect() as conn, self._lock:
            self._vectors = self._load_vectors(conn)
            self._stale = False
            self._loaded_at = time.monotonic()

    # ------------------------------------------------------------ search and indexing

    def search(self, query: str, *, kinds: Iterable[str] = (), roots: Iterable[str] = (),
               limit: int | None = None) -> SearchResponse:
        with self.connect() as conn:
            text_index, image_index = self.vectors(conn)
            return search(conn, query, self.cfg, text_index=text_index, image_index=image_index, kinds=kinds,
                          roots=roots, limit=limit)

    def index(self, folders: Iterable[Path | str], progress: ProgressFn | None = None,
              stop: threading.Event | None = None, listed: Callable[[], None] | None = None) -> IndexReport:
        """Index in two phases (SPEC §6.1): searches made meanwhile see listed files by name and native text at
        once, and by meaning and pictures as phase 2 reads them."""
        if self.read_only:
            raise PermissionError("this index is read-only")

        def reading(done: int, total: int, path: str, phase: str = "read") -> None:
            if phase == "read" and done:
                self._stale = True
            if progress:
                progress(done, total, path, phase=phase)

        with INDEX_LOCK:
            with self.connect() as conn:
                report = pipeline.index_paths(conn, folders, self.cfg, progress=reading, stop=stop, listed=listed)
            self.refresh()
        return report

    def remove_root(self, root: str) -> int:
        if self.read_only:
            raise PermissionError("this index is read-only")
        with INDEX_LOCK:
            with self.connect() as conn:
                removed = pipeline.remove_root(conn, root, self.cfg)
            self.refresh()
        return removed

    # ------------------------------------------------------------ files, by id only

    def _file(self, conn: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()

    def _paths(self, conn: sqlite3.Connection, file_id: int) -> list[str]:
        return [r[0] for r in conn.execute("SELECT path FROM paths WHERE file_id = ? ORDER BY mtime DESC", (file_id,))]

    def display(self, stored: str) -> str:
        """A stored path as the UI shows it."""
        if self.display_root is not None:
            p = Path(stored)
            if p.is_relative_to(self.display_root):
                return p.relative_to(self.display_root).as_posix()
        return stored

    def file_path(self, file_id: int) -> Path | None:
        """The file's primary path (most recently modified), if it's still there."""
        with self.connect() as conn:
            paths = self._paths(conn, file_id)
        for stored in paths:
            p = resolve_stored_path(stored, self.base_dir)
            if p.is_file():
                return p
        return None

    def thumb(self, file_id: int) -> Path | None:
        with self.connect() as conn:
            row = self._file(conn, file_id)
        if row is None or not row["thumb_path"]:
            return None
        for d in self.thumb_dirs:
            p = d / row["thumb_path"]
            if p.is_file():
                return p
        return None

    def thumb_image(self, file_id: int) -> Image.Image | None:
        p = self.thumb(file_id)
        if p is None:
            return None
        with Image.open(p) as im:
            return im.convert("RGB")

    def preview(self, file_id: int, page: int | None = None) -> Image.Image | None:
        """The detail panel's picture: an image upright, a PDF's `page` (else page 1) rendered, anything else its
        thumbnail card. A file that can't be read any more falls back to its thumbnail."""
        with self.connect() as conn:
            row = self._file(conn, file_id)
        if row is None:
            return None
        path = self.file_path(file_id)
        try:
            if path is not None and row["kind"] in ("photo", "screenshot"):
                return _fit(load_upright(path))
            if path is not None and row["kind"] == "pdf":
                return _render_pdf_page(path, page)
        except Exception:  # unreadable now (changed, corrupt): the thumbnail still shows what it was
            pass
        return self.thumb_image(file_id)

    def detail(self, file_id: int, chunk_id: int | None, query: str) -> Detail | None:
        with self.connect() as conn:
            row = self._file(conn, file_id)
            if row is None:
                return None
            paths = self._paths(conn, file_id)
            chunk = conn.execute("SELECT page, text FROM chunks WHERE id = ? AND file_id = ?",
                                 (chunk_id, file_id)).fetchone() if chunk_id is not None else None
        primary = Path(paths[0]) if paths else Path("")
        return Detail(
            file_id=file_id, kind=row["kind"], filename=primary.name, paths=[self.display(p) for p in paths],
            effective_date=row["effective_date"], page=chunk["page"] if chunk else None, pages=row["pages"],
            chunk_html=highlight(chunk["text"], query) if chunk else "",
        )

    def preview_data(self, file_id: int, chunk_id: int | None, page: int | None, query: str) -> dict | None:
        """What the preview fetches when a result is selected: highlight boxes (percent of the picture) and the
        matched passage as plain text (for Copy text)."""
        with self.connect() as conn:
            row = self._file(conn, file_id)
            if row is None:
                return None
            chunk = conn.execute("SELECT id, page, source, text FROM chunks WHERE id = ? AND file_id = ?",
                                 (chunk_id, file_id)).fetchone() if chunk_id is not None else None
            boxes: list[boxes_of.Box] = []
            try:
                if row["kind"] in ("photo", "screenshot") and chunk is not None and row["width"] and row["height"]:
                    boxes = boxes_of.image_boxes(conn, chunk["id"], query, row["width"], row["height"])
                elif row["kind"] == "pdf" and (path := self.file_path(file_id)) is not None:
                    boxes = boxes_of.pdf_boxes(conn, path, file_id, page or (chunk["page"] if chunk else 1) or 1,
                                                chunk, query)
            except Exception as e:  # an unreadable file: the preview just has no boxes
                log.warning("no highlight boxes for file %s: %s", file_id, e)
                boxes = []
        return {"boxes": [b.as_dict() for b in boxes], "text": chunk["text"] if chunk else ""}

    def download(self, file_id: int) -> tuple[Path, str] | None:
        """The file to send for a download, with the name to give it."""
        path = self.file_path(file_id)
        return (path, path.name) if path is not None else None

    def count(self, roots: Iterable[str] = ()) -> int:
        roots = list(roots)
        with self.connect() as conn:
            if not roots:
                return conn.execute("SELECT count(*) FROM files WHERE status = 'ok'").fetchone()[0]
            return conn.execute(
                f"SELECT count(*) FROM files f WHERE f.status = 'ok' AND EXISTS (SELECT 1 FROM paths p WHERE "
                f"p.file_id = f.id AND p.root IN ({','.join('?' * len(roots))}))", roots).fetchone()[0]

    def version(self) -> int:
        """Grows whenever indexing lists or reads a file (files + files read), so the UI can tell that results on
        screen are older than the index ("Update results", SPEC §8)."""
        with self.connect() as conn:
            files, ready = conn.execute("SELECT count(*), coalesce(sum(stage = 'ready'), 0) FROM files "
                                        "WHERE status = 'ok'").fetchone()
        return files + ready

    def type_counts(self) -> dict[str, int]:
        """Files per type for The Archive (SPEC §8): the kinds, with plain-text docs (`.txt`, `.md`) as "text"."""
        out = dict.fromkeys(TYPES, 0)
        with self.connect() as conn:
            for kind, is_text, n in conn.execute(
                    f"SELECT f.kind, f.kind = 'doc' AND {_IS_TEXT}, count(*) FROM files f WHERE f.status = 'ok' "
                    "GROUP BY 1, 2"):
                key = "text" if is_text else kind
                out[key] = out.get(key, 0) + n
        return out

    def recent(self, limit: int = 5, roots: Iterable[str] = ()) -> list[Recent]:
        """The most recently indexed files (the home screen's "Recently added")."""
        with self.connect() as conn:
            return self._recent(conn, limit, list(roots))

    def recent_by_type(self, limit: int = 5, roots: Iterable[str] = ()) -> list[Recent]:
        """The `limit` newest files overall and the `limit` newest of each type, newest first: what "Recently added"
        shows under each type filter on home (SPEC §8)."""
        roots = list(roots)
        found: dict[int, Recent] = {}
        with self.connect() as conn:
            for where in ["", *(_TYPE_WHERE[t] for t in TYPES)]:
                for r in self._recent(conn, limit, roots, where):
                    found.setdefault(r.file_id, r)
        return sorted(found.values(), key=lambda r: (r.indexed_at or "", r.file_id), reverse=True)

    def _recent(self, conn: sqlite3.Connection, limit: int, roots: list[str], where: str = "") -> list[Recent]:
        scope = (f"AND EXISTS (SELECT 1 FROM paths p WHERE p.file_id = f.id AND p.root IN ({','.join('?' * len(roots))}))"
                 if roots else "")
        rows = conn.execute(f"SELECT f.id, f.kind, f.indexed_at FROM files f WHERE f.status = 'ok' {where} {scope} "
                            "ORDER BY f.indexed_at DESC, f.id DESC LIMIT ?", [*roots, limit]).fetchall()
        out = []
        for r in rows:
            paths = self._paths(conn, r["id"])
            out.append(Recent(r["id"], Path(paths[0]).name if paths else "", r["kind"], r["indexed_at"]))
        return out

    def stats(self) -> Stats:
        with self.connect() as conn:
            counts = dict(conn.execute("SELECT kind, count(*) FROM files WHERE status = 'ok' GROUP BY kind").fetchall())
            errors = [dict(path=r[0], status=r[1], error=r[2] or "") for r in conn.execute(
                "SELECT p.path, f.status, f.error FROM files f JOIN paths p ON p.file_id = f.id "
                "WHERE f.status != 'ok' ORDER BY p.path LIMIT 100")]
            last = conn.execute("SELECT report_json FROM runs WHERE report_json IS NOT NULL "
                                "ORDER BY id DESC LIMIT 1").fetchone()
            roots, last_indexed = db.get_roots(conn), db.get_meta(conn, "last_indexed")
        size = self.db_path.stat().st_size if self.db_path.exists() else 0
        return Stats(counts, errors, roots, json.loads(last[0]) if last else None, last_indexed, size)


def _fit(img: Image.Image) -> Image.Image:
    """At most PREVIEW_WIDTH wide (a tall scroll keeps that width up to PREVIEW_MAX_HEIGHT); never enlarged."""
    w, h = img.size
    scale = min(1.0, PREVIEW_WIDTH / w, PREVIEW_MAX_HEIGHT / h)
    if scale < 1.0:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
    return img


def _render_pdf_page(path: Path, page: int | None) -> Image.Image:
    with pymupdf.open(path) as doc:
        index = page - 1 if page and 1 <= page <= doc.page_count else 0
        p = doc[index]
        zoom = PREVIEW_WIDTH / p.rect.width
        pix = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
