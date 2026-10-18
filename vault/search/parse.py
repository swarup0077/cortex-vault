"""Query parsing (SPEC §7.1). M4 builds the kind words only; dates and glue-word stripping are Later (§14).

Kind words become hard filters and leave the text that gets searched. The UI's kind chips use the same kinds
(`KIND_LABELS`), and chips and words combine as a union (SPEC §19, 18 Oct).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

# Kinds in chip order, with their chip labels.
KIND_LABELS: dict[str, str] = {
    "screenshot": "Screenshots", "photo": "Photos", "pdf": "PDFs", "doc": "Docs", "slides": "Slides",
}

# The M4 subset of the §7.1 table. "notes" is left out: it's a content noun as often as a kind word
# ("handwritten notes", "release notes"), and as a pdf + doc filter it would drop every image from such a query.
KIND_WORDS: dict[str, frozenset[str]] = {
    **dict.fromkeys(("screenshot", "screenshots"), frozenset({"screenshot"})),
    **dict.fromkeys(("photo", "photos", "picture", "pictures"), frozenset({"photo"})),
    **dict.fromkeys(("pdf", "pdfs"), frozenset({"pdf"})),
    **dict.fromkeys(("slide", "slides", "deck", "ppt"), frozenset({"slides"})),
    **dict.fromkeys(("doc", "docs"), frozenset({"pdf", "doc"})),
}

_QUOTED = re.compile(r'("[^"]*")')
_EDGE_PUNCT = ".,;:!?()[]{}'"


@dataclass(frozen=True)
class ParsedQuery:
    text: str
    kinds: frozenset[str] = frozenset()
    date_from: str | None = None  # ISO-8601, inclusive
    date_to: str | None = None  # ISO-8601, exclusive
    phrases: tuple[str, ...] = ()

    @property
    def filter_only(self) -> bool:
        return not self.text and not self.phrases


def _strip_kind_words(segment: str, found: set[str]) -> list[str]:
    """The segment's words minus kind words (matched without edge punctuation); their kinds go into `found`."""
    kept = []
    for word in segment.split():
        kinds = KIND_WORDS.get(word.strip(_EDGE_PUNCT).lower())
        if kinds:
            found.update(kinds)
        else:
            kept.append(word)
    return kept


def parse(query: str, now: datetime | None = None, kinds: Iterable[str] = ()) -> ParsedQuery:
    """Kind words in `query` (outside double quotes) become filters, united with the chips' `kinds`."""
    found: set[str] = set(kinds)
    words: list[str] = []
    for i, segment in enumerate(_QUOTED.split(query)):
        if i % 2:  # a quoted segment: kept as typed
            words.append(segment)
        else:
            words += _strip_kind_words(segment, found)
    return ParsedQuery(text=" ".join(w for w in words if w), kinds=frozenset(found))
