"""SQLite store: schema, transactions, explicit FTS sync (SPEC §5)."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SCHEMA_VERSION = "3"  # 2: path chunks are keyword-only (no vector); 3: files.ocr_cls

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  sha256 TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('pdf','doc','slides','screenshot','photo')),
  kind_reason TEXT,
  effective_date TEXT,
  date_source TEXT,
  width INTEGER, height INTEGER,
  pages INTEGER,
  gps_lat REAL, gps_lon REAL,
  thumb_path TEXT,
  ocr_cls INTEGER,
  status TEXT NOT NULL DEFAULT 'ok',
  error TEXT,
  indexed_at TEXT
);
CREATE TABLE IF NOT EXISTS paths (
  path TEXT PRIMARY KEY,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  root TEXT NOT NULL,
  size_bytes INTEGER NOT NULL, mtime REAL NOT NULL,
  seen_run INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS paths_file ON paths(file_id);
CREATE INDEX IF NOT EXISTS paths_root ON paths(root);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  page INTEGER,
  ord INTEGER NOT NULL,
  source TEXT NOT NULL,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_file ON chunks(file_id);
CREATE TABLE IF NOT EXISTS ocr_lines (
  id INTEGER PRIMARY KEY,
  chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  line_no INTEGER NOT NULL, block_no INTEGER NOT NULL,
  text TEXT NOT NULL, conf REAL,
  x0 REAL, y0 REAL, x1 REAL, y1 REAL
);
CREATE INDEX IF NOT EXISTS ocr_lines_chunk ON ocr_lines(chunk_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS text_vectors  (chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE, vec BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS image_vectors (file_id  INTEGER PRIMARY KEY REFERENCES files(id)  ON DELETE CASCADE, vec BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, report_json TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class ModelMismatchError(RuntimeError):
    """The index was built with different embedding models than the configured ones."""


def connect(db_path: Path | str, *, read_only: bool = False) -> sqlite3.Connection:
    """Open (creating if needed) the vault DB. Autocommit mode; use `transaction()` for writes.

    `read_only` opens an existing, current index that nothing can write to (the demo index, SPEC §2): no schema
    setup, no migration, and SQLite itself refuses writes.
    """
    if read_only:
        conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
        version = get_meta(conn, "schema_version")
        if version != SCHEMA_VERSION:
            conn.close()
            raise RuntimeError(f"{db_path} has schema {version}, not {SCHEMA_VERSION}: rebuild it with "
                               "`python -m vault build-demo`")
        return conn
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA secure_delete = ON")  # deleted rows are zeroed, not left in free space (SPEC §19)
    conn.executescript(SCHEMA)
    _fts_secure_delete(conn)
    version = get_meta(conn, "schema_version")
    if version is None:
        set_meta(conn, "schema_version", SCHEMA_VERSION)
    if version == "1":
        with transaction(conn):  # path chunks lost their vectors; their text is rewritten by the next index run
            conn.execute("DELETE FROM text_vectors WHERE chunk_id IN (SELECT id FROM chunks WHERE source = 'path')")
            set_meta(conn, "schema_version", version := "2")
    if version == "2":
        with transaction(conn):  # which angle-classifier setting each image's OCR used; before v3 that followed the
            # kind, with the default config: screenshots skipped the classifier, photos ran it
            if "ocr_cls" not in {r[1] for r in conn.execute("PRAGMA table_info(files)")}:
                conn.execute("ALTER TABLE files ADD COLUMN ocr_cls INTEGER")
            conn.execute("UPDATE files SET ocr_cls = (kind = 'photo') WHERE kind IN ('photo', 'screenshot') "
                         "AND status = 'ok'")
            set_meta(conn, "schema_version", "3")
    return conn


def _fts_secure_delete(conn: sqlite3.Connection) -> None:
    """Make FTS5 remove a deleted row's terms from its index at once. Without this they stay in the index
    blobs (as delete markers beside the old entries) until a segment merge, whatever `secure_delete` says.
    The option is stored in the table, so it's written once, not on every open."""
    row = conn.execute("SELECT v FROM chunks_fts_config WHERE k = 'secure-delete'").fetchone()
    if row is not None and row[0]:
        return
    try:
        conn.execute("INSERT INTO chunks_fts(chunks_fts, rank) VALUES('secure-delete', 1)")
    except sqlite3.OperationalError:  # FTS5 secure-delete needs SQLite 3.44+
        log.warning("SQLite %s can't purge deleted text from the FTS index until it merges; 3.44+ can",
                    sqlite3.sqlite_version)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """One atomic unit of work. Everything for one file goes in one of these (SPEC §5)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_roots(conn: sqlite3.Connection) -> list[str]:
    return json.loads(get_meta(conn, "roots") or "[]")


def add_roots(conn: sqlite3.Connection, roots: Iterable[str]) -> None:
    merged = list(dict.fromkeys([*get_roots(conn), *roots]))
    set_meta(conn, "roots", json.dumps(merged))


def set_roots(conn: sqlite3.Connection, roots: Iterable[str]) -> None:
    set_meta(conn, "roots", json.dumps(list(dict.fromkeys(roots))))


def check_model(conn: sqlite3.Connection, key: str, name: str, *, claim: bool = False) -> None:
    """Refuse a DB whose `meta[key]` names another model. With `claim`, record `name` when none is stored."""
    stored = get_meta(conn, key)
    if stored is None:
        if claim:
            set_meta(conn, key, name)
        return
    if stored != name:
        what = key.removesuffix("_model").replace("_", " ").replace("clip", "CLIP")
        raise ModelMismatchError(
            f"Index was built with {what} model {stored!r} but {name!r} is configured. "
            "Run `python -m vault reindex`."
        )


def check_text_model(conn: sqlite3.Connection, name: str, *, claim: bool = False) -> None:
    check_model(conn, "text_model", name, claim=claim)


def vec_to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype="<f4").tobytes()


def blob_to_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")


def insert_chunk(
    conn: sqlite3.Connection,
    file_id: int,
    page: int | None,
    ord_: int,
    source: str,
    text: str,
    vec: np.ndarray | None,
) -> int:
    """Insert a chunk with its FTS row and (optionally) its text vector."""
    cur = conn.execute(
        "INSERT INTO chunks(file_id, page, ord, source, text) VALUES (?, ?, ?, ?, ?)",
        (file_id, page, ord_, source, text),
    )
    chunk_id = cur.lastrowid
    conn.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)", (chunk_id, text))
    if vec is not None:
        conn.execute("INSERT INTO text_vectors(chunk_id, vec) VALUES (?, ?)", (chunk_id, vec_to_blob(vec)))
    return chunk_id


def set_image_vector(conn: sqlite3.Connection, file_id: int, vec: np.ndarray) -> None:
    conn.execute(
        "INSERT INTO image_vectors(file_id, vec) VALUES (?, ?) ON CONFLICT(file_id) DO UPDATE SET vec = excluded.vec",
        (file_id, vec_to_blob(vec)),
    )


def insert_ocr_lines(conn: sqlite3.Connection, chunk_id: int, lines: Iterable) -> None:
    """Store an OCR chunk's lines with their boxes (`OcrLine` records)."""
    conn.executemany(
        "INSERT INTO ocr_lines(chunk_id, line_no, block_no, text, conf, x0, y0, x1, y1) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(chunk_id, l.line, l.block, l.text, l.conf, l.x0, l.y0, l.x1, l.y1) for l in lines],
    )


def update_chunk_text(conn: sqlite3.Connection, chunk_id: int, text: str, vec: np.ndarray | None) -> None:
    """Replace a chunk's text, FTS row and vector, keeping its id. `vec=None` leaves it with no vector."""
    conn.execute("UPDATE chunks SET text = ? WHERE id = ?", (text, chunk_id))
    conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
    conn.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)", (chunk_id, text))
    if vec is None:
        conn.execute("DELETE FROM text_vectors WHERE chunk_id = ?", (chunk_id,))
        return
    conn.execute(
        "INSERT INTO text_vectors(chunk_id, vec) VALUES (?, ?) "
        "ON CONFLICT(chunk_id) DO UPDATE SET vec = excluded.vec",
        (chunk_id, vec_to_blob(vec)),
    )


def delete_content_chunks(conn: sqlite3.Connection, file_id: int) -> None:
    """Delete a file's content chunks (not its path chunk) with their FTS rows; OCR lines and vectors cascade."""
    conn.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE file_id = ? AND source != 'path')",
                 (file_id,))
    conn.execute("DELETE FROM chunks WHERE file_id = ? AND source != 'path'", (file_id,))


def delete_file(conn: sqlite3.Connection, file_id: int) -> None:
    """Delete a file and all its rows. FTS5 has no cascade, so its rows go explicitly."""
    conn.execute(
        "DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE file_id = ?)", (file_id,)
    )
    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))


def compact(conn: sqlite3.Connection) -> None:
    """Merge the FTS index into one segment and rewrite the file without free pages, so nothing from deleted
    files is left in it, including leftovers from before secure delete was on. Runs outside a transaction."""
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('optimize')")
    conn.execute("VACUUM")


def wipe_index(conn: sqlite3.Connection) -> None:
    """Remove every indexed file (for `reindex`). Keeps roots and run history."""
    conn.execute("DELETE FROM chunks_fts")
    conn.execute("DELETE FROM files")
    conn.execute("DELETE FROM meta WHERE key IN ('text_model', 'text_dim', 'clip_model')")
