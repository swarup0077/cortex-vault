"""Highlight boxes for the preview (SPEC §7.6, §8), as percentages of the picture so they fit at any size.

Images: the OCR lines of the matched chunk that hold a query word. PDF pages: the PDF's own text layer
(`page.search_for`, at request time) plus the OCR lines of that page. Query words shorter than 3 characters and
stopwords are ignored, and a page gets at most `MAX_BOXES`. A meaning-only match (no query word in the text) gets
one lighter box around the matched passage's OCR lines instead; a native text layer has no passage boxes.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf

from vault.search.snippet import _stem
from vault.search.terms import STOPWORDS

MAX_BOXES = 10
MIN_TERM = 3


@dataclass(frozen=True)
class Box:
    x: float  # left, % of the picture's width
    y: float  # top, % of its height
    w: float
    h: float
    kind: str = "term"  # "term": a line or word holding a query word; "passage": the meaning-only fallback

    def as_dict(self) -> dict:
        return {k: round(v, 3) if isinstance(v, float) else v for k, v in asdict(self).items()}


def query_terms(query: str) -> list[str]:
    """Query words worth a box: not stopwords, at least 3 characters, crudely stemmed like the snippet's."""
    words = dict.fromkeys(w for w in re.findall(r"\w+", query.lower()) if w not in STOPWORDS and len(w) >= MIN_TERM)
    return list(dict.fromkeys(st for w in words if len(st := _stem(w)) >= MIN_TERM))


def _pattern(terms: Sequence[str]) -> re.Pattern[str] | None:
    if not terms:
        return None
    alts = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"\b(?:{alts})", re.IGNORECASE)


def _pct(x0: float, y0: float, x1: float, y1: float, width: float, height: float, kind: str = "term") -> Box | None:
    if width <= 0 or height <= 0 or x1 <= x0 or y1 <= y0:
        return None
    clamp = lambda v: min(max(v, 0.0), 100.0)  # noqa: E731
    x, y = clamp(100 * x0 / width), clamp(100 * y0 / height)
    return Box(x, y, clamp(100 * x1 / width) - x, clamp(100 * y1 / height) - y, kind)


def line_boxes(lines: Iterable[tuple], terms: Sequence[str], width: float, height: float,
               passage: bool = True) -> list[Box]:
    """Boxes on the lines (text, x0, y0, x1, y1) holding a term; with none and `passage`, one box around them all."""
    lines = list(lines)
    pat = _pattern(terms)
    hits = [ln for ln in lines if pat and pat.search(ln[0])]
    if hits:
        return [b for ln in hits[:MAX_BOXES] if (b := _pct(*ln[1:5], width, height))]
    if passage and lines:
        box = _pct(min(ln[1] for ln in lines), min(ln[2] for ln in lines), max(ln[3] for ln in lines),
                   max(ln[4] for ln in lines), width, height, "passage")
        return [box] if box else []
    return []


def image_boxes(conn: sqlite3.Connection, chunk_id: int, query: str, width: int, height: int) -> list[Box]:
    lines = [tuple(r) for r in conn.execute("SELECT text, x0, y0, x1, y1 FROM ocr_lines WHERE chunk_id = ? "
                                            "ORDER BY line_no", (chunk_id,))]
    return line_boxes(lines, query_terms(query), width, height)


def pdf_boxes(conn: sqlite3.Connection, path: Path, file_id: int, page: int, chunk: sqlite3.Row | None,
              query: str) -> list[Box]:
    """The page's text layer and OCR lines, in percent of the page as rendered (rotation applied)."""
    terms = query_terms(query)
    with pymupdf.open(path) as doc:
        if not 1 <= page <= doc.page_count:
            return []
        p = doc[page - 1]
        shown = p.rect  # the page as the reader sees it (rotated)
        to_shown = p.rotation_matrix  # stored and searched coordinates are unrotated points
        boxes: list[Box] = []
        for term in terms:
            for r in p.search_for(term):
                r = r * to_shown
                if b := _pct(r.x0, r.y0, r.x1, r.y1, shown.width, shown.height):
                    boxes.append(b)
        rows = conn.execute(
            "SELECT l.text, l.x0, l.y0, l.x1, l.y1, l.chunk_id FROM ocr_lines l JOIN chunks c ON c.id = l.chunk_id "
            "WHERE c.file_id = ? AND c.page = ? ORDER BY l.chunk_id, l.line_no", (file_id, page)).fetchall()

        def rotated(row):
            r = pymupdf.Rect(row[1], row[2], row[3], row[4]) * to_shown
            return (row[0], r.x0, r.y0, r.x1, r.y1)

        lines = [rotated(r) for r in rows]
        boxes += line_boxes(lines, terms, shown.width, shown.height, passage=False)
        if not boxes and chunk is not None and chunk["source"] == "ocr":  # meaning-only, in an OCR'd region
            mine = [rotated(r) for r in rows if r[5] == chunk["id"]]
            boxes = line_boxes(mine, [], shown.width, shown.height)
    return boxes[:MAX_BOXES]
