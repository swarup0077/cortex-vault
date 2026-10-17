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

SCHEMA_VERSION = "1"

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


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) the vault DB. Autocommit mode; use `transaction()` for writes."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA secure_delete = ON")  # deleted rows are zeroed, not left in free space (SPEC §19)
    conn.executescript(SCHEMA)
    _fts_secure_delete(conn)
    if get_meta(conn, "schema_version") is None:
        set_meta(conn, "schema_version", SCHEMA_VERSION)
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


def check_text_model(conn: sqlite3.Connection, name: str, *, claim: bool = False) -> None:
    """Refuse a DB built with another text model. With `claim`, record `name` on an empty DB."""
    stored = get_meta(conn, "text_model")
    if stored is None:
        if claim:
            set_meta(conn, "text_model", name)
        return
    if stored != name:
        raise ModelMismatchError(
            f"Index was built with text model {stored!r} but {name!r} is configured. "
            "Run `python -m vault reindex`."
        )


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


def insert_ocr_lines(conn: sqlite3.Connection, chunk_id: int, lines: Iterable) -> None:
    """Store an OCR chunk's lines with their boxes (`OcrLine` records)."""
    conn.executemany(
        "INSERT INTO ocr_lines(chunk_id, line_no, block_no, text, conf, x0, y0, x1, y1) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(chunk_id, l.line, l.block, l.text, l.conf, l.x0, l.y0, l.x1, l.y1) for l in lines],
    )


def update_chunk_text(conn: sqlite3.Connection, chunk_id: int, text: str, vec: np.ndarray) -> None:
    """Replace a chunk's text, FTS row and vector, keeping its id."""
    conn.execute("UPDATE chunks SET text = ? WHERE id = ?", (text, chunk_id))
    conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
    conn.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)", (chunk_id, text))
    conn.execute(
        "INSERT INTO text_vectors(chunk_id, vec) VALUES (?, ?) "
        "ON CONFLICT(chunk_id) DO UPDATE SET vec = excluded.vec",
        (chunk_id, vec_to_blob(vec)),
    )


def delete_file(conn: sqlite3.Connection, file_id: int) -> None:
    """Delete a file and all its rows. FTS5 has no cascade, so its rows go explicitly."""
    conn.execute(
        "DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE file_id = ?)", (file_id,)
    )
    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))


def wipe_index(conn: sqlite3.Connection) -> None:
    """Remove every indexed file (for `reindex`). Keeps roots and run history."""
    conn.execute("DELETE FROM chunks_fts")
    conn.execute("DELETE FROM files")
    conn.execute("DELETE FROM meta WHERE key IN ('text_model', 'text_dim')")
