"""Retrievers. Each applies the parsed kind/date filters before ranking (SPEC §7.2)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np

from vault.models import ClipText, TextEmbedder
from vault.search.parse import ParsedQuery
from vault.search.terms import content_terms
from vault.vectors import VectorIndex


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    file_id: int
    score: float


def filter_sql(pq: ParsedQuery, alias: str = "f") -> tuple[str, list]:
    """SQL condition over `files` for the parsed filters (always excludes error/skipped files)."""
    conds, params = [f"{alias}.status = 'ok'"], []
    if pq.kinds:
        conds.append(f"{alias}.kind IN ({','.join('?' * len(pq.kinds))})")
        params += sorted(pq.kinds)
    if pq.date_from:
        conds.append(f"{alias}.effective_date >= ?")
        params.append(pq.date_from)
    if pq.date_to:
        conds.append(f"{alias}.effective_date < ?")
        params.append(pq.date_to)
    return " AND ".join(conds), params


def allowed_file_ids(conn: sqlite3.Connection, pq: ParsedQuery) -> set[int] | None:
    """File ids passing the filters, or None when there's no kind/date filter (everything indexed is ok)."""
    if not (pq.kinds or pq.date_from or pq.date_to):
        return None
    where, params = filter_sql(pq)
    return {r[0] for r in conn.execute(f"SELECT f.id FROM files f WHERE {where}", params)}


def _quote(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def fts_query(pq: ParsedQuery) -> str | None:
    """Content terms OR'ed (stopwords dropped while another term remains), required phrases AND'ed. Every term is
    quoted, so user input can't inject FTS syntax."""
    any_of = " OR ".join(_quote(t) for t in content_terms(pq.text))
    all_of = " AND ".join(_quote(p) for p in pq.phrases)
    if any_of and all_of:
        return f"({any_of}) AND {all_of}"
    return any_of or all_of or None


def fts_all_query(pq: ParsedQuery) -> str | None:
    """Every content term AND'ed, plus the required phrases: chunks that hold the whole query (SPEC §7.2)."""
    terms = content_terms(pq.text)
    if not terms:
        return None
    return " AND ".join([_quote(t) for t in terms] + [_quote(p) for p in pq.phrases])


def bm25(conn: sqlite3.Connection, pq: ParsedQuery, k: int, *, all_terms: bool = False) -> list[Hit]:
    """BM25 over chunks. With `all_terms`, only chunks containing every content term (the all-terms list)."""
    q = fts_all_query(pq) if all_terms else fts_query(pq)
    if q is None:
        return []
    where, params = filter_sql(pq)
    rows = conn.execute(
        "SELECT c.id, c.file_id, bm25(chunks_fts) AS s FROM chunks_fts "
        "JOIN chunks c ON c.id = chunks_fts.rowid JOIN files f ON f.id = c.file_id "
        f"WHERE chunks_fts MATCH ? AND {where} ORDER BY s LIMIT ?",
        [q, *params, k],
    ).fetchall()
    return [Hit(r[0], r[1], -r[2]) for r in rows]  # FTS5 bm25 is lower-is-better


def load_text_index(conn: sqlite3.Connection, dim: int) -> VectorIndex:
    return VectorIndex.from_query(
        conn, "SELECT v.chunk_id, v.vec, c.file_id FROM text_vectors v JOIN chunks c ON c.id = v.chunk_id", dim
    )


def dense(
    conn: sqlite3.Connection,
    pq: ParsedQuery,
    k: int,
    embedder: TextEmbedder,
    index: VectorIndex,
    allowed: set[int] | None,
    min_cosine: float = -1.0,
) -> list[Hit]:
    """Top-k chunks by cosine, dropping any less similar than `min_cosine` (SPEC §7.2)."""
    if not pq.text or not len(index):
        return []
    mask = index.mask_groups(allowed) if allowed is not None else None
    top = [(cid, s) for cid, s in index.search(embedder.embed_query(pq.text), k, mask) if s >= min_cosine]
    if not top:
        return []
    ids = [cid for cid, _ in top]
    file_of = dict(conn.execute(f"SELECT id, file_id FROM chunks WHERE id IN ({','.join('?' * len(ids))})", ids))
    return [Hit(cid, file_of[cid], score) for cid, score in top]


def load_image_index(conn: sqlite3.Connection) -> VectorIndex:
    """CLIP vectors, one per image file; the group is the file itself (for the filter mask)."""
    return VectorIndex.from_query(conn, "SELECT file_id, vec, file_id FROM image_vectors ORDER BY file_id")


def clip_scores(pq: ParsedQuery, model: ClipText, index: VectorIndex,
                allowed: set[int] | None) -> tuple[np.ndarray, np.ndarray]:
    """cosine(clip_text(query), image) for every filter-passing image: the gate needs all of them (SPEC §7.3)."""
    if not pq.text or not len(index):
        return np.zeros(0, np.int64), np.zeros(0, np.float32)
    mask = index.mask_groups(allowed) if allowed is not None else None
    return index.scores(model.embed_text(pq.text), mask)
