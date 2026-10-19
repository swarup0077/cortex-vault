"""Collapse chunk hits to files, then Reciprocal Rank Fusion (SPEC §7.4)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from vault.search.retrievers import Hit


@dataclass
class Fused:
    file_id: int
    score: float
    ranks: dict[str, int] = field(default_factory=dict)  # retriever -> 1-based file rank
    chunks: dict[str, int] = field(default_factory=dict)  # retriever -> best chunk id
    shares: dict[str, float] = field(default_factory=dict)  # retriever -> its term of the RRF sum


def collapse(hits: Sequence[Hit]) -> list[tuple[int, int]]:
    """Keep each file's best-ranked chunk, in order: [(file_id, chunk_id)]. Position + 1 is the file's rank.
    A contents page stands for its file only when none of the file's other chunks is in the list (open item n)."""
    first: dict[int, tuple[int, int]] = {}  # file → (position in the list, chunk)
    toc_only: dict[int, tuple[int, int]] = {}
    for i, h in enumerate(hits):
        (toc_only if getattr(h, "toc", False) else first).setdefault(h.file_id, (i, h.chunk_id))
    for file_id, at in toc_only.items():
        first.setdefault(file_id, at)
    return [(file_id, chunk) for file_id, (_, chunk) in sorted(first.items(), key=lambda kv: kv[1][0])]


def rrf(
    lists: Mapping[str, Sequence[tuple[int, int]]], k: int = 60, weights: Mapping[str, float] | None = None,
    boosts: Mapping[str, Mapping[int, float]] | None = None,
) -> list[Fused]:
    """score(f) = Σ_r w_r · b_r(f) / (k + rank_r(f)); a file missing from a list contributes 0 for it.

    `boosts[list][file_id]` multiplies one file's weight in one list (default 1); its rank is unchanged.
    """
    fused: dict[int, Fused] = {}
    for name, ranked in lists.items():
        w = (weights or {}).get(name, 1.0)
        boost = (boosts or {}).get(name, {})
        for rank, (file_id, chunk_id) in enumerate(ranked, start=1):
            f = fused.setdefault(file_id, Fused(file_id, 0.0))
            share = w * boost.get(file_id, 1.0) / (k + rank)
            f.score += share
            f.shares[name] = share
            f.ranks[name] = rank
            f.chunks[name] = chunk_id
    return sorted(fused.values(), key=lambda f: (-f.score, min(f.ranks.values()), f.file_id))
