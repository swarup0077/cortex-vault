"""`search()`: the one entry point the CLI and UI call (SPEC §3, §7)."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from vault import db, models
from vault.config import Config
from vault.search import retrievers
from vault.search.fuse import Fused, collapse, rrf
from vault.search.parse import ParsedQuery, parse
from vault.search.snippet import make_snippet
from vault.vectors import VectorIndex


@dataclass
class SearchResult:
    """One card. Fields follow SPEC §7.7; image-only fields stay empty until M2/M3."""

    file_id: int
    kind: str
    filename: str
    folder: str
    paths: list[str]
    copies: int
    effective_date: str | None
    page: int | None
    chunk_id: int | None
    snippet_html: str
    ranks: dict[str, int | None]
    rrf_score: float
    badges: list[str] = field(default_factory=list)
    thumb_url: str | None = None
    highlight_boxes: list = field(default_factory=list)
    clip_gated: bool = False
    rerank_score: float | None = None


@dataclass
class SearchResponse:
    query: str
    parsed: ParsedQuery
    results: list[SearchResult]
    timings_ms: dict[str, float]


class _Stopwatch:
    def __init__(self) -> None:
        self.ms: dict[str, float] = {}
        self._t = time.perf_counter()

    def lap(self, stage: str) -> None:
        now = time.perf_counter()
        self.ms[stage] = round((now - self._t) * 1000, 2)
        self._t = now


def _file_rows(conn: sqlite3.Connection, file_ids: list[int]) -> tuple[dict[int, sqlite3.Row], dict[int, list[str]]]:
    if not file_ids:
        return {}, {}
    marks = ",".join("?" * len(file_ids))
    files = {r["id"]: r for r in conn.execute(f"SELECT * FROM files WHERE id IN ({marks})", file_ids)}
    paths: dict[int, list[str]] = {}
    for r in conn.execute(f"SELECT file_id, path FROM paths WHERE file_id IN ({marks}) ORDER BY mtime DESC", file_ids):
        paths.setdefault(r[0], []).append(r[1])  # most recently modified first: that's the primary path
    return files, paths


def _result(file: sqlite3.Row, paths: list[str], fused: Fused | None, chunk: sqlite3.Row | None, query: str, cfg: Config) -> SearchResult:
    primary = Path(paths[0]) if paths else Path("")
    return SearchResult(
        file_id=file["id"],
        kind=file["kind"],
        filename=primary.name,
        folder=str(primary.parent),
        paths=paths,
        copies=max(len(paths) - 1, 0),
        effective_date=file["effective_date"],
        page=chunk["page"] if chunk else None,
        chunk_id=chunk["id"] if chunk else None,
        snippet_html=make_snippet(chunk["text"], query, cfg.snippet_chars) if chunk else "",
        ranks={name: fused.ranks.get(name) for name in cfg.weights} if fused else {},
        rrf_score=fused.score if fused else 0.0,
    )


def search(
    conn: sqlite3.Connection,
    query: str,
    cfg: Config | None = None,
    *,
    embedder: models.TextEmbedder | None = None,
    text_index: VectorIndex | None = None,
    now: datetime | None = None,
    limit: int | None = None,
) -> SearchResponse:
    """Parse → BM25 + dense → collapse to file → RRF → snippet.

    Pass a prebuilt `text_index` to avoid reloading vectors on every query (the UI does; the CLI needn't).
    """
    cfg = cfg or Config()
    limit = limit or cfg.max_results
    sw = _Stopwatch()
    pq = parse(query, now)
    sw.lap("parse")

    allowed = retrievers.allowed_file_ids(conn, pq)
    if pq.filter_only:  # no text: filtered files, newest first
        where, params = retrievers.filter_sql(pq)
        ids = [r[0] for r in conn.execute(
            f"SELECT f.id FROM files f WHERE {where} ORDER BY f.effective_date DESC LIMIT ?", [*params, limit]
        )]
        files, paths = _file_rows(conn, ids)
        results = [_result(files[i], paths.get(i, []), None, None, "", cfg) for i in ids]
        sw.lap("filter")
        return SearchResponse(query, pq, results, sw.ms)

    embedder = embedder or models.text_embedder(cfg)
    db.check_text_model(conn, embedder.name)
    bm25_hits = retrievers.bm25(conn, pq, cfg.top_k)
    sw.lap("bm25")
    if text_index is None:
        text_index = retrievers.load_text_index(conn, embedder.dim)
        sw.lap("load_vectors")
    dense_hits = retrievers.dense(conn, pq, cfg.top_k, embedder, text_index, allowed)
    sw.lap("dense")

    fused = rrf({"bm25": collapse(bm25_hits), "dense": collapse(dense_hits)}, cfg.rrf_k, cfg.weights)[:limit]
    sw.lap("fuse")

    # Snippet chunk: BM25's best chunk for the file if it has one, else dense's.
    best_chunk = {f.file_id: f.chunks.get("bm25", f.chunks.get("dense")) for f in fused}
    chunk_ids = [c for c in best_chunk.values() if c is not None]
    chunks = {
        r["id"]: r
        for r in conn.execute(
            f"SELECT id, page, text FROM chunks WHERE id IN ({','.join('?' * len(chunk_ids))})", chunk_ids
        )
    } if chunk_ids else {}
    files, paths = _file_rows(conn, [f.file_id for f in fused])
    results = [
        _result(files[f.file_id], paths.get(f.file_id, []), f, chunks.get(best_chunk[f.file_id]), pq.text, cfg)
        for f in fused
    ]
    sw.lap("hydrate")
    return SearchResponse(query, pq, results, sw.ms)
