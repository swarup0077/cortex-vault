"""CLIP gate (SPEC §7.3): keep CLIP's statistical outliers only, so it can't tie BM25 when no image answers.

CLIP ranks every image for every query, even `error 0x80070005`. In RRF its rank 1 counts as much as BM25's.
The gate keeps an image only when it scores well above the other candidates, and halves CLIP's weight for
quoted or code-like queries, where exact text is what matters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from vault.config import Config
from vault.search.parse import ParsedQuery

_HEX = re.compile(r"0x[0-9a-f]+", re.IGNORECASE)
_LONG_DIGITS = re.compile(r"\d{6,}")


@dataclass(frozen=True)
class Gated:
    hits: list[tuple[int, float]]  # (file_id, cosine), best first
    gated: bool  # the z-score filter ran (False: too few candidates, the top few were kept as they are)
    weight_factor: float  # multiplies CLIP's RRF weight
    candidates: int


def is_code_like(text: str) -> bool:
    """A hex value (`0x80070005`), a run of 6+ digits, or a 4+ character token mixing letters and digits."""
    for tok in re.findall(r"\w+", text):
        if _HEX.fullmatch(tok) or _LONG_DIGITS.fullmatch(tok):
            return True
        if len(tok) >= 4 and any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok):
            return True
    return False


def gate(ids: np.ndarray, scores: np.ndarray, pq: ParsedQuery, cfg: Config) -> Gated:
    """Which CLIP hits enter fusion. `ids`/`scores` cover every filter-passing candidate image."""
    factor = cfg.clip_code_weight if (pq.phrases or is_code_like(pq.text)) else 1.0
    n = len(scores)
    order = np.argsort(-scores, kind="stable")  # ties keep the given (file id) order
    if n < cfg.clip_gate_min_candidates:  # too few for a z-score: the top few, if similar enough at all
        keep = [i for i in order[: cfg.clip_gate_fallback_top] if scores[i] >= cfg.clip_min_cosine]
        return Gated([(int(ids[i]), float(scores[i])) for i in keep], False, factor, n)
    std = float(scores.std())
    if std == 0.0:  # every image scores the same: none stands out
        return Gated([], True, factor, n)
    z = (scores - scores.mean()) / std
    keep = [i for i in order if z[i] >= cfg.clip_gate_z][: cfg.clip_gate_max]
    return Gated([(int(ids[i]), float(scores[i])) for i in keep], True, factor, n)
