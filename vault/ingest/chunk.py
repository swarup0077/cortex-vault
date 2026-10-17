"""Chunk extracted text by structure (SPEC §6.4)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from vault.config import Config
from vault.ingest.records import Extracted, Segment


@dataclass(frozen=True)
class Chunk:
    text: str
    page: int | None
    source: str


def normalise(text: str) -> str:
    """Collapse whitespace runs; keep casing (FTS5 case-folds)."""
    return re.sub(r"\s+", " ", text).strip()


def windows(text: str, size: int, overlap: int) -> list[str]:
    """~`size`-char windows overlapping by ~`overlap` chars, cut on spaces where possible."""
    if len(text) <= size:
        return [text]
    out, start = [], 0
    while True:
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind(" ", start + size // 2, end)
            if cut > start:
                end = cut
        out.append(text[start:end].strip())
        if end >= len(text):
            return out
        nxt = max(end - overlap, start + 1)
        space = text.find(" ", nxt, end)
        start = space + 1 if space != -1 else nxt


def _overlap_tail(paras: Sequence[str], overlap: int) -> list[str]:
    """Trailing whole paragraphs that fit in `overlap` chars, else the last ~`overlap` chars of the last one."""
    tail: list[str] = []
    total = 0
    for p in reversed(paras):
        if total + len(p) > overlap:
            break
        tail.insert(0, p)
        total += len(p) + 1
    if tail or not paras:
        return tail
    last = paras[-1]
    start = last.find(" ", len(last) - overlap)
    return [last[start + 1 :]] if start != -1 else []


def chunk_paragraphs(paras: Iterable[str], target: int, overlap: int, max_len: int) -> list[str]:
    """~`target`-char chunks cut on paragraph boundaries; a paragraph over `max_len` is windowed alone."""
    chunks: list[str] = []
    cur: list[str] = []
    fresh = False  # cur holds at least one paragraph not already emitted
    for p in map(normalise, paras):
        if not p:
            continue
        if len(p) > max_len:
            if fresh:
                chunks.append("\n".join(cur))
            chunks.extend(windows(p, target, overlap))
            cur, fresh = [], False
            continue
        if fresh and len("\n".join([*cur, p])) > target:
            chunks.append("\n".join(cur))
            cur = _overlap_tail(cur, overlap)
        cur.append(p)
        fresh = True
    if fresh:
        chunks.append("\n".join(cur))
    return chunks


def chunk_pages(segments: Iterable[Segment], cfg: Config) -> list[Chunk]:
    """One chunk per page/slide; over `chunk_max` chars it's windowed, keeping the page number."""
    out = []
    for seg in segments:
        text = normalise(seg.text)
        if not text:
            continue
        parts = windows(text, cfg.chunk_target, cfg.chunk_overlap) if len(text) > cfg.chunk_max else [text]
        out += [Chunk(t, seg.page, seg.source) for t in parts]
    return out


def chunk_extracted(ex: Extracted, cfg: Config) -> list[Chunk]:
    paged = [s for s in ex.segments if s.page is not None]
    flowing = [s for s in ex.segments if s.page is None]
    chunks = chunk_pages(paged, cfg)
    if flowing:
        texts = chunk_paragraphs((s.text for s in flowing), cfg.chunk_target, cfg.chunk_overlap, cfg.chunk_max)
        chunks += [Chunk(t, None, flowing[0].source) for t in texts]
    return chunks


def path_chunk_text(paths: Iterable[Path]) -> str:
    """`Parent folder / filename` for every path of a file, one per line (SPEC §5)."""
    lines = {f"{p.parent.name} / {p.name}" if p.parent.name else p.name for p in paths}
    return "\n".join(sorted(lines))
