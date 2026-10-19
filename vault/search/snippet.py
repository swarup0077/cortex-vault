"""~200-char snippet around the query terms, with <mark> highlights (SPEC §7.4)."""

from __future__ import annotations

import html
import re

from vault.search.terms import STOPWORDS
_SUFFIXES = ("ations", "ation", "ings", "ing", "ies", "ied", "es", "ed", "s")


def _stem(term: str) -> str:
    for suf in _SUFFIXES:
        if term.endswith(suf) and len(term) - len(suf) >= 3:
            return term[: -len(suf)]
    return term


def term_pattern(query: str) -> re.Pattern[str] | None:
    """Words of the query (minus stopwords), crudely stemmed, matching at word starts."""
    terms = {_stem(t) for t in re.findall(r"\w+", query.lower()) if t not in STOPWORDS and len(t) > 1}
    if not terms:
        return None
    alts = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"\b(?:{alts})\w*", re.IGNORECASE)


def _best_start(matches: list[re.Match[str]], width: int) -> int:
    """Window start (a quarter-width before some match) that covers the most matches; earliest wins ties."""
    best_start, best_count = 0, 0
    for m in matches:
        s = max(0, m.start() - width // 4)
        count = sum(1 for n in matches if n.start() >= s and n.end() <= s + width)
        if count > best_count:
            best_start, best_count = s, count
    return best_start


_TOKEN = re.compile(r"\S+")
_PROSE_WORD = re.compile(r"[\"'(\[]?[^\W\d_]{2,}[.,;:!?)\]\"']*")


def prose_start(text: str, run: int = 3) -> int | None:
    """Where the first `run` words of letters in a row begin: the text's first prose, past a status bar
    ("08:12 87% Browser") or a window's title bar ("Documents Installer □ X"). None: no prose at all."""
    tokens = list(_TOKEN.finditer(text))
    for i in range(len(tokens) - run + 1):
        if all(_PROSE_WORD.fullmatch(t.group()) for t in tokens[i:i + run]):
            return tokens[i].start()
    return None


def make_snippet(text: str, query: str, width: int = 200, prose_run: int = 3) -> str:
    """HTML-escaped window of `text` covering the most query-term matches, matches wrapped in <mark>. With no
    match (a meaning-only hit), the window starts at the text's first prose, not at its status or title bar
    (open item y)."""
    pat = term_pattern(query)
    matches = list(pat.finditer(text)) if pat else []
    if matches:
        start = _best_start(matches, width) if len(text) > width else 0
    else:
        start = prose_start(text, prose_run) or 0
    end = min(len(text), start + width)
    if start > 0 and matches:
        space = text.find(" ", start, start + 30)
        start = space + 1 if space != -1 else start
    if end < len(text):
        space = text.rfind(" ", end - 30, end)
        end = space if space > start else end

    return ("…" if start > 0 else "") + _marked(text, matches, start, end) + ("…" if end < len(text) else "")


def _marked(text: str, matches: list[re.Match[str]], start: int, end: int) -> str:
    """`text[start:end]` HTML-escaped, with the matches inside it wrapped in <mark>."""
    out, pos = [], start
    for m in matches:
        if m.start() < start or m.end() > end:
            continue
        out.append(html.escape(text[pos : m.start()]))
        out.append(f"<mark>{html.escape(m.group())}</mark>")
        pos = m.end()
    out.append(html.escape(text[pos:end]))
    return "".join(out)


def highlight(text: str, query: str) -> str:
    """The whole text, HTML-escaped, with the query terms in <mark> (the detail panel's full chunk)."""
    pat = term_pattern(query)
    return _marked(text, list(pat.finditer(text)) if pat else [], 0, len(text))
