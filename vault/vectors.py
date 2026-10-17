"""Exact top-k over an in-memory vector matrix (SPEC §7.2, §11.4). No vector DB."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

import numpy as np


class VectorIndex:
    """Rows of L2-normalised float32 vectors with their ids, and optional group ids
    (e.g. the file each chunk belongs to) for filtering."""

    def __init__(self, ids: np.ndarray, matrix: np.ndarray, groups: np.ndarray | None = None) -> None:
        self.ids = np.asarray(ids, dtype=np.int64)
        self.matrix = np.asarray(matrix, dtype=np.float32)
        self.groups = None if groups is None else np.asarray(groups, dtype=np.int64)

    @classmethod
    def from_query(cls, conn: sqlite3.Connection, sql: str, dim: int | None = None) -> VectorIndex:
        """`sql` returns (id, vec_blob) or (id, vec_blob, group) rows. `dim=None` takes it from the blobs."""
        rows = conn.execute(sql).fetchall()
        if not rows:
            return cls(np.zeros(0), np.zeros((0, dim or 0)), np.zeros(0))
        dim = dim or len(rows[0][1]) // 4
        ids = np.fromiter((r[0] for r in rows), np.int64, len(rows))
        matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype="<f4").reshape(len(rows), dim)
        groups = np.fromiter((r[2] for r in rows), np.int64, len(rows)) if len(rows[0]) > 2 else None
        return cls(ids, matrix, groups)

    def __len__(self) -> int:
        return len(self.ids)

    def mask_groups(self, allowed: Iterable[int]) -> np.ndarray:
        if self.groups is None:
            raise ValueError("index has no groups")
        return np.isin(self.groups, np.fromiter(allowed, np.int64))

    def scores(self, query: np.ndarray, mask: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
        """(ids, cosines) for every row, or only the rows where `mask` is True (the CLIP gate needs them all)."""
        if not len(self):
            return np.zeros(0, np.int64), np.zeros(0, np.float32)
        s = self.matrix @ np.asarray(query, dtype=np.float32)
        return (self.ids, s) if mask is None else (self.ids[mask], s[mask])

    def search(self, query: np.ndarray, k: int, mask: np.ndarray | None = None) -> list[tuple[int, float]]:
        """Top-k (id, cosine) by one matmul and argpartition. `mask` keeps rows where True."""
        if not len(self):
            return []
        scores = self.matrix @ np.asarray(query, dtype=np.float32)
        if mask is not None:
            scores = np.where(mask, scores, -np.inf)
            k = min(k, int(mask.sum()))
        k = min(k, len(scores))
        if k <= 0:
            return []
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top], kind="stable")]
        return [(int(self.ids[i]), float(scores[i])) for i in top]
