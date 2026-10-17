"""OCR layout (SPEC §6.3): tiling, box mapping, overlap dedup, digit fixes, reading order and blocks.

Only `ocr_image` touches a model; everything else works on `OcrLine` boxes, so it's tested without one.
"""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image

from vault.config import Config
from vault.ingest.records import OcrLine
from vault.models import OcrEngine

EDGE_PX = 2  # a box this close to a strip's inner cut edge was cut by it


# ---------------------------------------------------------------- tiling


@dataclass(frozen=True)
class Tiling:
    size: tuple[int, int]  # working image (w, h) after any scaling
    strips: list[tuple[int, int]]  # (top, bottom) rows of the working image


def plan_tiles(width: int, height: int, cfg: Config) -> Tiling:
    """Scale to at most `ocr_max_side` wide; cut a still-too-tall image into overlapping strips (§6.3).

    Strips are spread evenly, so every overlap is at least `ocr_strip_overlap`. An image needing more than
    `ocr_max_strips` is scaled down until it fits.
    """
    scale = min(1.0, cfg.ocr_max_side / width)
    h = height * scale
    if h > cfg.ocr_max_side:
        step = cfg.ocr_strip - cfg.ocr_strip_overlap
        tallest = step * (cfg.ocr_max_strips - 1) + cfg.ocr_strip
        if h > tallest:
            scale *= tallest / h
    w, h = max(1, round(width * scale)), max(1, round(height * scale))
    if h <= cfg.ocr_max_side:
        return Tiling((w, h), [(0, h)])
    n = math.ceil((h - cfg.ocr_strip_overlap) / (cfg.ocr_strip - cfg.ocr_strip_overlap))
    pitch = (h - cfg.ocr_strip) / (n - 1)
    tops = [round(i * pitch) for i in range(n)]
    return Tiling((w, h), [(t, t + cfg.ocr_strip) for t in tops])


# ---------------------------------------------------------------- text fixes

_O_IN_DIGITS = re.compile(r"(?<=\d)O+(?=\d)")
_HEX_PREFIX = re.compile(r"\b[Oo](?=[xX][0-9A-Fa-f]{4,}\b)")


def fix_digits(text: str) -> str:
    """Undo OCR's O/0 confusion where it's unambiguous: `Ox80070005` → `0x80070005`, `8OO7` → `8007`."""
    text = _O_IN_DIGITS.sub(lambda m: "0" * len(m.group()), text)
    return _HEX_PREFIX.sub("0", text)


_CJK = "㐀-䶿一-鿿豈-﫿"  # CJK ideographs: unified, extension A, compatibility
_CJK_CHAR = re.compile(f"[{_CJK}]")
_LONE_CJK = re.compile(f"(?<![{_CJK}])[{_CJK}](?![{_CJK}])")
_LATIN = re.compile(r"[A-Za-z]")


def drop_stray_cjk(text: str) -> str:
    """In a line with more Latin letters than CJK characters, drop CJK characters standing alone: icons the
    Chinese-English recogniser misread (`品 Settings` → `Settings`). Runs of two or more are kept."""
    n_cjk = len(_CJK_CHAR.findall(text))
    if not n_cjk or len(_LATIN.findall(text)) <= n_cjk:
        return text
    return " ".join(_LONE_CJK.sub(" ", text).split())


def clean_text(text: str) -> str:
    return fix_digits(drop_stray_cjk(" ".join(text.split())))


def _is_cjk_fragment(text: str) -> bool:
    """One or two CJK characters and nothing else but spaces and punctuation (`品`, `白 这`, `米。`)."""
    core = [c for c in text if c.isalnum()]
    return 1 <= len(core) <= 2 and all(_CJK_CHAR.match(c) for c in core)


def drop_cjk_fragments(lines: Sequence[OcrLine], latin_share: float) -> list[OcrLine]:
    """Drop lines that are only one or two CJK characters when the rest of the image is mostly Latin (at least
    `latin_share` of its letters): icons read on their own. Real CJK text, a run of 3+ characters or an image
    that is mostly CJK, is kept."""
    rest = [ln for ln in lines if not _is_cjk_fragment(ln.text)]
    if len(rest) == len(lines):
        return list(lines)
    latin = sum(len(_LATIN.findall(ln.text)) for ln in rest)
    cjk = sum(len(_CJK_CHAR.findall(ln.text)) for ln in rest)
    if latin == 0 or latin < latin_share * (latin + cjk):
        return list(lines)
    return rest


# ---------------------------------------------------------------- strips → one set of lines


def _x_overlap(a: OcrLine, b: OcrLine) -> float:
    return max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))


def _same_line(a: OcrLine, b: OcrLine) -> bool:
    """Same text at the same place: vertical centres within half a line height, boxes overlapping sideways."""
    if a.text.lower() != b.text.lower():
        return False
    if abs(a.cy - b.cy) > 0.5 * min(a.height, b.height):
        return False
    return _x_overlap(a, b) > 0.5 * min(a.x1 - a.x0, b.x1 - b.x0)


def merge_strips(per_strip: Sequence[Sequence[OcrLine]], tiling: Tiling, orig_size: tuple[int, int],
                 cfg: Config) -> list[OcrLine]:
    """Raw lines per strip (strip pixels) → one list in original pixels.

    Drops low-confidence lines and lines cut by an inner strip edge, cleans the text, removes the duplicates
    read twice in an overlap, and drops lone CJK lines (misread icons) from a mostly Latin image.
    """
    sx, sy = tiling.size[0] / orig_size[0], tiling.size[1] / orig_size[1]
    last = len(tiling.strips) - 1
    kept: list[list[OcrLine]] = []
    for i, ((top, bottom), raw) in enumerate(zip(tiling.strips, per_strip)):
        lines = []
        for ln in raw:
            text = clean_text(ln.text)
            if ln.conf < cfg.ocr_min_conf or not text:
                continue
            if (i > 0 and ln.y0 <= EDGE_PX) or (i < last and ln.y1 >= bottom - top - EDGE_PX):
                continue  # the neighbouring strip has this line whole
            lines.append(replace(ln, text=text, x0=ln.x0 / sx, x1=ln.x1 / sx,
                                 y0=(ln.y0 + top) / sy, y1=(ln.y1 + top) / sy))
        kept.append(lines)
    for i in range(last):  # duplicates can only sit in the overlap of neighbouring strips
        lo, hi = tiling.strips[i + 1][0] / sy, tiling.strips[i][1] / sy
        upper = [ln for ln in kept[i] if lo <= ln.cy <= hi]
        drop_lower, drop_upper = set(), set()
        for j, b in enumerate(kept[i + 1]):
            if not lo <= b.cy <= hi:
                continue
            for a in upper:
                if id(a) not in drop_upper and _same_line(a, b):
                    if b.conf > a.conf:
                        drop_upper.add(id(a))
                    else:
                        drop_lower.add(j)
                    break
        kept[i] = [ln for ln in kept[i] if id(ln) not in drop_upper]
        kept[i + 1] = [ln for j, ln in enumerate(kept[i + 1]) if j not in drop_lower]
    return drop_cjk_fragments([ln for lines in kept for ln in lines], cfg.ocr_latin_share)


def ocr_image(img: Image.Image, engine: OcrEngine, cfg: Config, *, use_cls: bool | None = None) -> list[OcrLine]:
    """OCR an RGB image strip by strip. Lines are in original pixels, cleaned and deduplicated, not ordered."""
    tiling = plan_tiles(img.width, img.height, cfg)
    work = img if tiling.size == img.size else img.resize(tiling.size, Image.Resampling.LANCZOS)
    arr = np.asarray(work)
    per_strip = [engine.read(arr[top:bottom], use_cls=use_cls) for top, bottom in tiling.strips]
    return merge_strips(per_strip, tiling, img.size, cfg)


# ---------------------------------------------------------------- reading order and blocks


def _rows(lines: Sequence[OcrLine]) -> list[list[OcrLine]]:
    """Group lines by vertical centre: ≥ 50% vertical overlap with a row's first line joins it. Left to right."""
    rows: list[list[OcrLine]] = []
    for ln in sorted(lines, key=lambda l: (l.cy, l.x0)):
        if rows:
            anchor = rows[-1][0]
            overlap = min(anchor.y1, ln.y1) - max(anchor.y0, ln.y0)
            if overlap >= 0.5 * min(anchor.height, ln.height):
                rows[-1].append(ln)
                continue
        rows.append([ln])
    return [sorted(r, key=lambda l: l.x0) for r in rows]


def _gaps(intervals: Sequence[tuple[float, float]], min_gap: float) -> list[tuple[float, float]]:
    """Empty stretches strictly wider than `min_gap` between the intervals' union pieces."""
    ivs = sorted(intervals)
    out, end = [], ivs[0][1]
    for a, b in ivs[1:]:
        if a - end > min_gap:
            out.append((end, a))
        end = max(end, b)
    return out


def _gutters(lines: Sequence[OcrLine], h: float, cfg: Config) -> list[tuple[float, float]]:
    """Vertical gutters across the whole region with at least two rows on each side, widest first."""
    if len(lines) < 4:
        return []
    out = []
    for a, b in _gaps([(l.x0, l.x1) for l in lines], cfg.ocr_gutter * h):
        left = [l for l in lines if l.x1 <= a]
        right = [l for l in lines if l.x0 >= b]
        if len(_rows(left)) >= 2 and len(_rows(right)) >= 2:
            out.append((a, b))
    return sorted(out, key=lambda g: g[0] - g[1])


def _intersect(gs: Sequence[tuple[float, float]], hs: Sequence[tuple[float, float]],
               min_width: float) -> list[tuple[float, float]]:
    out = []
    for a0, a1 in gs:
        for b0, b1 in hs:
            lo, hi = max(a0, b0), min(a1, b1)
            if hi - lo > min_width:
                out.append((lo, hi))
    return out


def _xy_cut(lines: list[OcrLine], h: float, cfg: Config) -> list[list[OcrLine]]:
    """Split into layout regions in reading order: column gutters first, then wide horizontal bands."""
    gutters = _gutters(lines, h, cfg)
    if gutters:
        mid = sum(gutters[0]) / 2  # the gutter is empty, so every line is wholly on one side
        left = [l for l in lines if l.x1 <= mid]
        right = [l for l in lines if l.x0 >= mid]
        return _xy_cut(left, h, cfg) + _xy_cut(right, h, cfg)

    bands = _gaps([(l.y0, l.y1) for l in lines], cfg.ocr_block_gap * h) if len(lines) > 1 else []
    if not bands:
        return [lines]
    cuts = [(a + b) / 2 for a, b in bands]
    slabs: list[list[OcrLine]] = [[] for _ in range(len(cuts) + 1)]
    for l in lines:
        slabs[sum(l.cy > c for c in cuts)].append(l)
    # Rejoin neighbouring slabs that share a column gutter, so paragraph breaks lining up across columns
    # don't interleave them.
    groups: list[list[OcrLine]] = []
    shared: list[tuple[float, float]] = []
    for slab in slabs:
        mine = _gutters(slab, h, cfg)
        common = _intersect(shared, mine, cfg.ocr_gutter * h) if groups else []
        if common:
            groups[-1] += slab
            shared = common
        else:
            groups.append(list(slab))
            shared = mine
    if len(groups) == 1:  # can't happen (the whole region would have a gutter), but never loop
        groups = slabs
    return [region for g in groups for region in _xy_cut(g, h, cfg)]


def reading_order(lines: Sequence[OcrLine], cfg: Config) -> list[OcrLine]:
    """Lines in reading order, with `block` and `line` set (SPEC §6.3)."""
    if not lines:
        return []
    h = max(statistics.median(l.height for l in lines), 1e-6)
    out: list[OcrLine] = []
    block = -1
    for region in _xy_cut(list(lines), h, cfg):
        prev_bottom: float | None = None
        for row in _rows(region):
            top = min(l.y0 for l in row)
            if prev_bottom is None or top - prev_bottom > cfg.ocr_block_gap * h:
                block += 1
            out += [replace(l, block=block, line=len(out) + k) for k, l in enumerate(row)]
            prev_bottom = max(l.y1 for l in row)
    return out


def blocks(lines: Sequence[OcrLine]) -> list[list[OcrLine]]:
    """Consecutive runs of lines sharing a block number."""
    out: list[list[OcrLine]] = []
    for ln in lines:
        if out and out[-1][-1].block == ln.block:
            out[-1].append(ln)
        else:
            out.append([ln])
    return out


def lines_text(lines: Sequence[OcrLine]) -> str:
    """The chunk text for some lines: their (cleaned) texts joined by spaces, so the two always agree."""
    return " ".join(l.text for l in lines)
