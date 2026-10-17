"""Query parsing (SPEC §7.1). Until M5 this passes the text through unchanged (SPEC §19, 17 Oct)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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


def parse(query: str, now: datetime | None = None) -> ParsedQuery:
    return ParsedQuery(text=" ".join(query.split()))
