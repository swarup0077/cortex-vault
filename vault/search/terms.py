"""Query terms, shared by BM25 and snippet highlighting so they agree on what a query word is."""

from __future__ import annotations

import re

STOPWORDS = frozenset(
    "a an and are as at be by for from how i in is it my of on or that the this to was what when where which who"
    " why with about".split()
)


def tokens(text: str) -> list[str]:
    """Lower-cased word tokens, first occurrence order, no repeats."""
    return list(dict.fromkeys(re.findall(r"\w+", text.lower())))


def content_terms(text: str) -> list[str]:
    """Tokens minus stopwords; all tokens when the query is nothing but stopwords ("the who")."""
    toks = tokens(text)
    return [t for t in toks if t not in STOPWORDS] or toks
