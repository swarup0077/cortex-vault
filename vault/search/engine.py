"""`search()`: the one entry point the CLI and UI call (SPEC §3, §7)."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

import numpy as np

from vault import db, models
from vault.config import Config
from vault.search import retrievers
from vault.search.fuse import Fused, collapse, rrf
from vault.search.gate import Gated, gate
from vault.search.parse import ParsedQuery, parse
from vault.search.snippet import make_snippet
from vault.vectors import VectorIndex

SNIPPET_ORDER = ("all_terms", "bm25", "dense")


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
    weaker: bool = False  # below the fused list: only the dense floor or the CLIP gate kept it out (SPEC §7.4)
    # Why this result (SPEC §8): `ranks` are each method's real rank (for a weaker match, in the method's full list,
    # below the floor or outside the gate); `shares` are each list's term of the RRF sum that placed it
    shares: dict[str, float] = field(default_factory=dict)
    clip_status: str | None = None  # images, when CLIP ran: "strong", "kept" (by the gate) or "not kept"
    # Facts for the preview
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    pages: int | None = None
    chunk_source: str | None = None  # 'pdf_text' | 'ocr' | 'doc_text' | 'slide_text' of the snippet chunk
    text_sources: list[str] = field(default_factory=list)  # every content-chunk source the file has


@dataclass
class SearchResponse:
    query: str
    parsed: ParsedQuery
    results: list[SearchResult]
    timings_ms: dict[str, float]
    clip: dict[str, object] = field(default_factory=dict)  # candidates, kept, strong, gated, weight, visual
    weights: dict[str, float] = field(default_factory=dict)  # each list's RRF weight for this query
    weaker: list[SearchResult] = field(default_factory=list)  # "Weaker matches", shown below `results`
    visual: list[SearchResult] = field(default_factory=list)  # CLIP's gated hits, best first: the Visual matches row


class _Stopwatch:
    def __init__(self) -> None:
        self.ms: dict[str, float] = {}
        self._t = time.perf_counter()

    def lap(self, stage: str) -> None:
        now = time.perf_counter()
        self.ms[stage] = round((now - self._t) * 1000, 2)
        self._t = now


def _file_rows(conn: sqlite3.Connection, file_ids: list[int]) -> tuple[dict[int, sqlite3.Row], dict[int, list[str]]]:
    files, paths, _ = _file_info(conn, file_ids)
    return files, paths


def _file_info(conn: sqlite3.Connection, file_ids: list[int]
               ) -> tuple[dict[int, sqlite3.Row], dict[int, list[str]], dict[int, int]]:
    """files rows, each file's paths (primary first) and the primary path's size."""
    if not file_ids:
        return {}, {}, {}
    marks = ",".join("?" * len(file_ids))
    files = {r["id"]: r for r in conn.execute(f"SELECT * FROM files WHERE id IN ({marks})", file_ids)}
    paths: dict[int, list[str]] = {}
    sizes: dict[int, int] = {}
    for r in conn.execute(f"SELECT file_id, path, size_bytes FROM paths WHERE file_id IN ({marks}) ORDER BY mtime DESC",
                          file_ids):
        paths.setdefault(r[0], []).append(r[1])  # most recently modified first: that's the primary path
        sizes.setdefault(r[0], r[2])
    return files, paths, sizes


@dataclass(frozen=True)
class _Clip:
    """What the CLIP gate did with this query's images, for each result's `clip_status`."""

    ran: bool = False
    kept: frozenset[int] = frozenset()
    strong: frozenset[int] = frozenset()

    def status(self, file_id: int, kind: str) -> str | None:
        if not self.ran or kind not in ("photo", "screenshot"):
            return None
        return "strong" if file_id in self.strong else "kept" if file_id in self.kept else "not kept"


def _result(file: sqlite3.Row, paths: list[str], fused: Fused | None, chunk: sqlite3.Row | None, query: str, cfg: Config,
            clip_gated: bool = False, weaker: bool = False, *, size: int | None = None, sources: Sequence[str] = (),
            clip: _Clip = _Clip(), real_ranks: dict[str, dict[int, int]] | None = None) -> SearchResult:
    primary = Path(paths[0]) if paths else Path("")
    ranks = {name: fused.ranks.get(name) for name in cfg.weights} if fused else {}
    if real_ranks is not None:  # a weaker match: each method's rank in its full list, not in the tail's fusion
        ranks = {name: (real_ranks.get(name, {}).get(file["id"]) if r is not None else None) for name, r in ranks.items()}
    visual_only = fused is not None and set(fused.ranks) == {"clip"}  # found by CLIP alone: no text to show
    badges = (["weaker match"] if weaker else []) + (["visual match"] if visual_only else [])
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
        ranks=ranks,
        rrf_score=fused.score if fused else 0.0,
        badges=badges,
        thumb_url=file["thumb_path"],
        clip_gated=clip_gated,
        weaker=weaker,
        shares=dict(fused.shares) if fused else {},
        clip_status=clip.status(file["id"], file["kind"]),
        size_bytes=size,
        width=file["width"],
        height=file["height"],
        pages=file["pages"],
        chunk_source=chunk["source"] if chunk else None,
        text_sources=list(sources),
    )


def _hydrate(conn: sqlite3.Connection, fused: list[Fused], query: str, cfg: Config, clip_gated: bool,
             weaker: bool = False, *, clip: _Clip = _Clip(),
             real_ranks: dict[str, dict[int, int]] | None = None) -> list[SearchResult]:
    """Cards for fused files, in order. Snippet chunk: the one holding every term if any, else BM25's best for
    the file, else dense's."""
    best_chunk = {f.file_id: next((f.chunks[n] for n in SNIPPET_ORDER if n in f.chunks), None) for f in fused}
    chunk_ids = [c for c in best_chunk.values() if c is not None]
    chunks = {
        r["id"]: r
        for r in conn.execute(
            f"SELECT id, page, text, source FROM chunks WHERE id IN ({','.join('?' * len(chunk_ids))})", chunk_ids
        )
    } if chunk_ids else {}
    ids = [f.file_id for f in fused]
    files, paths, sizes = _file_info(conn, ids)
    sources: dict[int, list[str]] = {}
    if ids:
        for fid, src in conn.execute("SELECT DISTINCT file_id, source FROM chunks WHERE source != 'path' AND file_id IN "
                                     f"({','.join('?' * len(ids))}) ORDER BY source", ids):
            sources.setdefault(fid, []).append(src)
    return [
        _result(files[f.file_id], paths.get(f.file_id, []), f, chunks.get(best_chunk[f.file_id]), query, cfg,
                clip_gated, weaker, size=sizes.get(f.file_id), sources=sources.get(f.file_id, ()), clip=clip,
                real_ranks=real_ranks)
        for f in fused
    ]


def search(
    conn: sqlite3.Connection,
    query: str,
    cfg: Config | None = None,
    *,
    embedder: models.TextEmbedder | None = None,
    text_index: VectorIndex | None = None,
    clip: models.ClipText | None = None,
    image_index: VectorIndex | None = None,
    now: datetime | None = None,
    limit: int | None = None,
    kinds: Iterable[str] = (),
    roots: Iterable[str] = (),
) -> SearchResponse:
    """Parse → BM25 + all-terms + dense + gated CLIP → collapse to file → RRF (with the visual-query rule) → snippet,
    then the weaker matches below (SPEC §7.4 step 4) and CLIP's gated hits for the Visual matches row.

    `kinds` are the UI's kind chips, united with the query's kind words; `roots` limits every list to files with
    a path under those indexed roots (the demo's upload scope). Pass prebuilt `text_index` / `image_index` to
    avoid reloading vectors on every query (the UI does; the CLI needn't). The CLIP text model loads only when
    the index holds image vectors.
    """
    cfg = cfg or Config()
    limit = limit or cfg.max_results
    sw = _Stopwatch()
    pq = parse(query, now, kinds)
    if roots:
        pq = replace(pq, roots=frozenset(str(r) for r in roots))
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
    sw.lap("load_model")  # ~0 once warm; the first query pays the model load
    bm25_hits = retrievers.bm25(conn, pq, cfg.top_k)
    sw.lap("bm25")
    all_hits = retrievers.bm25(conn, pq, cfg.top_k, all_terms=True)
    sw.lap("all_terms")
    if text_index is None:
        text_index = retrievers.load_text_index(conn, embedder.dim, cfg.dense_min_word)
        sw.lap("load_vectors")
    # The floor applies after top-k, so filtering here is the same as in the retriever; the rest feeds the tail.
    dense_all = retrievers.dense(conn, pq, cfg.top_k, embedder, text_index, allowed)
    dense_hits = [h for h in dense_all if h.score >= cfg.dense_min_cosine]
    sw.lap("dense")

    gated = Gated([], False, 1.0, 0)
    clip_ranked: list[int] = []  # every candidate image by cosine, ungated (for the tail)
    if cfg.weights.get("clip", 1.0) > 0:
        if image_index is None:
            image_index = retrievers.load_image_index(conn)
            sw.lap("load_images")
        if len(image_index):
            clip = clip or models.clip_text(cfg)
            db.check_model(conn, "clip_model", clip.name)
            ids, scores = retrievers.clip_scores(pq, clip, image_index, allowed)
            gated = gate(ids, scores, pq, cfg)
            clip_ranked = [int(ids[i]) for i in np.argsort(-scores, kind="stable")[: cfg.top_k]]
            sw.lap("clip")

    lists = {"bm25": collapse(bm25_hits), "all_terms": collapse(all_hits), "dense": collapse(dense_hits),
             "clip": [(file_id, None) for file_id, _ in gated.hits]}
    lists = {name: ranked for name, ranked in lists.items() if cfg.weights.get(name, 1.0) > 0}
    weights = {**cfg.weights, "clip": cfg.weights.get("clip", 1.0) * gated.weight_factor}
    # Visual-query rule (SPEC §7.4): no file holds every word, so BM25 has only partial matches, and CLIP has a
    # strong outlier. Without it, any file in two lists (2/61) beats CLIP's #1 alone (1/61), however strong.
    visual = bool(gated.strong) and not all_hits and gated.weight_factor == 1.0
    boosts: dict[str, dict[int, float]] = {}
    if visual:
        weights["bm25"] = weights.get("bm25", 1.0) * cfg.visual_bm25_factor
        boosts["clip"] = dict.fromkeys(gated.strong, cfg.visual_clip_factor)
    fused_all = rrf(lists, cfg.rrf_k, weights, boosts)
    fused = fused_all[:limit]
    sw.lap("fuse")

    # Weaker matches (SPEC §7.4 step 4): what only the dense floor or the CLIP gate kept out, fused the same way
    # without them, below everything fusion kept. BM25 has no floor, so none of its files is left for here.
    tail: list[Fused] = []
    if cfg.weaker_matches and len(fused) < limit:
        shown = {f.file_id for f in fused}
        rest = {"dense": [x for x in collapse(dense_all) if x[0] not in shown],
                "clip": [(file_id, None) for file_id in clip_ranked if file_id not in shown]}
        rest = {name: ranked for name, ranked in rest.items() if cfg.weights.get(name, 1.0) > 0}
        tail = rrf(rest, cfg.rrf_k, weights)[: limit - len(fused)]
        sw.lap("weaker")

    clip_info = _Clip(bool(clip_ranked), frozenset(f for f, _ in gated.hits), gated.strong)
    real_ranks = {"dense": {f: i for i, (f, _) in enumerate(collapse(dense_all), start=1)},
                  "clip": {f: i for i, f in enumerate(clip_ranked, start=1)}}
    results = _hydrate(conn, fused, pq.text, cfg, gated.gated, clip=clip_info)
    weaker = _hydrate(conn, tail, pq.text, cfg, False, weaker=True, clip=clip_info, real_ranks=real_ranks)
    by_id = {r.file_id: r for r in results}
    row_ids = [file_id for file_id, _ in gated.hits[: cfg.visual_row]]
    fused_by_id = {f.file_id: f for f in fused_all}
    missing = _hydrate(conn, [fused_by_id[i] for i in row_ids if i not in by_id], pq.text, cfg, gated.gated,
                       clip=clip_info)
    by_id.update({r.file_id: r for r in missing})
    sw.lap("hydrate")
    clip_summary = {"candidates": gated.candidates, "kept": len(gated.hits), "strong": len(gated.strong),
                    "gated": gated.gated, "weight": weights["clip"], "visual": visual}
    return SearchResponse(query, pq, results, sw.ms, clip_summary, weights, weaker, [by_id[i] for i in row_ids])
