"""Retrievers. Each applies the parsed kind/date filters before ranking (SPEC §7.2)."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from vault.models import TextEmbedder
from vault.search.parse import ParsedQuery
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
    """Text tokens OR'ed, required phrases AND'ed. Every term is quoted, so user input can't inject FTS syntax."""
    tokens = list(dict.fromkeys(re.findall(r"\w+", pq.text.lower())))
    any_of = " OR ".join(_quote(t) for t in tokens)
    all_of = " AND ".join(_quote(p) for p in pq.phrases)
    if any_of and all_of:
        return f"({any_of}) AND {all_of}"
    return any_of or all_of or None


def bm25(conn: sqlite3.Connection, pq: ParsedQuery, k: int) -> list[Hit]:
    q = fts_query(pq)
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
) -> list[Hit]:
    if not pq.text or not len(index):
        return []
    mask = index.mask_groups(allowed) if allowed is not None else None
    top = index.search(embedder.embed_query(pq.text), k, mask)
    if not top:
        return []
    ids = [cid for cid, _ in top]
    file_of = dict(conn.execute(f"SELECT id, file_id FROM chunks WHERE id IN ({','.join('?' * len(ids))})", ids))
    return [Hit(cid, file_of[cid], score) for cid, score in top]
