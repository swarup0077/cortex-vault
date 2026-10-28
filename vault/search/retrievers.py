"""Retrievers. Each applies the parsed kind/date filters before ranking (SPEC §7.2)."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

import numpy as np

from vault.models import ClipText, TextEmbedder
from vault.search.parse import ParsedQuery
from vault.search.terms import content_terms
from vault.search.toc import is_toc
from vault.vectors import VectorIndex


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    file_id: int
    score: float
    toc: bool = False  # a contents page (open item n): it stands for its file only when nothing else of it does


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
    if pq.roots:
        conds.append(f"EXISTS (SELECT 1 FROM paths p WHERE p.file_id = {alias}.id "
                     f"AND p.root IN ({','.join('?' * len(pq.roots))}))")
        params += sorted(pq.roots)
    return " AND ".join(conds), params


def allowed_file_ids(conn: sqlite3.Connection, pq: ParsedQuery) -> set[int] | None:
    """File ids passing the filters, or None when there's no kind/date/scope filter (everything indexed is ok)."""
    if not (pq.kinds or pq.date_from or pq.date_to or pq.roots):
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


def bm25(conn: sqlite3.Connection, pq: ParsedQuery, k: int, *, all_terms: bool = False, toc_min: int = 0,
         per_file: int = 10) -> list[Hit]:
    """BM25 over chunks, for the k files whose best chunk ranks highest (at most `per_file` chunks each): k files,
    however many chunks one long document has (open item f). With `all_terms`, only chunks containing every content
    term (the all-terms list). Hits on a contents page (`toc_min` dot-leader entries, open item n) are marked."""
    q = fts_all_query(pq) if all_terms else fts_query(pq)
    if q is None:
        return []
    where, params = filter_sql(pq)
    if toc_min > 0:
        conn.create_function("cv_is_toc", 1, lambda text: is_toc(text, toc_min), deterministic=True)
    toc = "cv_is_toc(c.text)" if toc_min > 0 else "0"  # only on the rows kept: it reads each chunk's text
    rows = conn.execute(
        "WITH m AS (SELECT c.id, c.file_id, bm25(chunks_fts) AS s FROM chunks_fts "
        "JOIN chunks c ON c.id = chunks_fts.rowid JOIN files f ON f.id = c.file_id "
        f"WHERE chunks_fts MATCH ? AND {where}), "
        "r AS (SELECT id, file_id, s, row_number() OVER (PARTITION BY file_id ORDER BY s, id) AS n FROM m), "
        "top AS (SELECT file_id FROM r WHERE n = 1 ORDER BY s, id LIMIT ?) "
        f"SELECT r.id, r.file_id, r.s, {toc} FROM r JOIN chunks c ON c.id = r.id "
        "WHERE r.n <= ? AND r.file_id IN (SELECT file_id FROM top) ORDER BY r.s, r.id",
        [q, *params, k, per_file],
    ).fetchall()
    return [Hit(r[0], r[1], -r[2], bool(r[3])) for r in rows]  # FTS5 bm25 is lower-is-better


def terms_held(conn: sqlite3.Connection, pq: ParsedQuery, chunk_ids: list[int]) -> dict[int, int]:
    """How many of the query's content terms each chunk holds, matched the way BM25 matches them."""
    held = dict.fromkeys(chunk_ids, 0)
    if not chunk_ids:
        return held
    marks = ",".join("?" * len(chunk_ids))
    for t in content_terms(pq.text):
        for (rowid,) in conn.execute(f"SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? AND rowid IN ({marks})",
                                     [_quote(t), *chunk_ids]):
            held[rowid] += 1
    return held


_WORD = re.compile(r"[^\W_]+")


def longest_word(text: str | None) -> int:
    """Characters in the longest run of letters or digits (an ideograph counts as one)."""
    return max((len(w) for w in _WORD.findall(text or "")), default=0)


def load_text_index(conn: sqlite3.Connection, dim: int, min_word: int = 0, toc_min: int = 0) -> VectorIndex:
    """Chunk vectors for dense search. Left out, and kept in BM25: a chunk with no word of `min_word` characters
    (OCR junk such as "0 田": no meaning, yet close to many short queries, open item x), and a contents page
    (`toc_min` dot-leader entries: it names every topic in a few words, open item n)."""
    conds = []
    if min_word > 0:
        conn.create_function("cv_longest_word", 1, longest_word, deterministic=True)
        conds.append(f"cv_longest_word(c.text) >= {int(min_word)}")
    if toc_min > 0:
        conn.create_function("cv_is_toc", 1, lambda text: is_toc(text, toc_min), deterministic=True)
        conds.append("NOT cv_is_toc(c.text)")
    where = f" WHERE {' AND '.join(conds)}" if conds else ""
    return VectorIndex.from_query(
        conn, f"SELECT v.chunk_id, v.vec, c.file_id FROM text_vectors v JOIN chunks c ON c.id = v.chunk_id{where}", dim
    )


def dense(
    conn: sqlite3.Connection,
    pq: ParsedQuery,
    k: int,
    embedder: TextEmbedder,
    index: VectorIndex,
    allowed: set[int] | None,
    min_cosine: float = -1.0,
    per_file: int = 10,
) -> list[Hit]:
    """Chunks by cosine for the k most similar files (at most `per_file` chunks each, open item f), dropping any
    less similar than `min_cosine` (SPEC §7.2)."""
    if not pq.text or not len(index):
        return []
    mask = index.mask_groups(allowed) if allowed is not None else None
    top = [(cid, s) for cid, s in index.search_files(embedder.embed_query(pq.text), k, mask, per_file)
           if s >= min_cosine]
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


def keyword_places(conn: sqlite3.Connection, pq: ParsedQuery, file_ids: list[int]) -> dict[int, set[str]]:
    """Where the query's words occur in each file: "text" (a content chunk) and/or "path" (the chunk holding its file
    name and folders), matched the way BM25 matches them. It asks the index directly, not the BM25 hits (at most
    `chunks_per_file` chunks a file), so a long file's name match isn't missed."""
    q = fts_query(pq)
    out: dict[int, set[str]] = {}
    if q is None or not file_ids:
        return out
    marks = ",".join("?" * len(file_ids))
    for file_id, source in conn.execute(
            "SELECT DISTINCT c.file_id, c.source FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
            f"WHERE chunks_fts MATCH ? AND c.file_id IN ({marks})", [q, *file_ids]):
        out.setdefault(file_id, set()).add("path" if source == "path" else "text")
    return out
