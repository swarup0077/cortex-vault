"""Contents pages (open item n): a book's table of contents names every topic in a few words, so BM25 ranks it
above the page that covers the topic. A page is a contents page when it has `toc_min_entries` or more dot-leader
entries ("Bayes' theorem ........ 17"), as native text or as OCR reads them ("…", spaced dots). Detected from the
stored chunk text, so existing indexes are flagged without re-extracting.
"""

from __future__ import annotations

import re

# A run of leader dots (or an ellipsis character, or spaced dots) and then a page number: 1-9999, not a price
# ("120.00") or part of a longer number, and not followed by a word ("....2 marks", an exam paper read by OCR).
_LEADER = re.compile(r"(?:\.{2,}|…+|·{2,}|(?:\. ){2,}\.?)\s*[1-9]\d{0,3}(?![\d.,]\d|\d)(?!\s*[a-z(])")
_RUN = re.compile(r"(?:\.{3,}|…+|·{3,}|(?:\. ){3,}\.?)")


def leader_entries(text: str | None) -> int:
    """How many "title ..... page" entries the text has."""
    return len(_LEADER.findall(text or ""))


def is_toc(text: str | None, min_entries: int) -> bool:
    return min_entries > 0 and leader_entries(text) >= min_entries


def strip_leaders(text: str) -> str:
    """Leader runs shown as a single "…" (a contents page's snippet), so they don't fill the window."""
    return _RUN.sub(" … ", text)
