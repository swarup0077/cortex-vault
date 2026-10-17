"""Records passed between ingest stages."""

from __future__ import annotations

from dataclasses import dataclass, field


class SkipFile(Exception):
    """A file we deliberately don't index (encrypted PDF, zip guard). Recorded as status='skipped'."""


@dataclass(frozen=True)
class Segment:
    """A piece of extracted text. `page` is the PDF page or slide (1-based), or None for flowing docs,
    where each segment is one paragraph."""

    text: str
    page: int | None
    source: str  # 'pdf_text' | 'doc_text' | 'slide_text'


@dataclass
class Extracted:
    segments: list[Segment] = field(default_factory=list)
    pages: int | None = None
    doc_date: str | None = None  # ISO-8601 from document metadata, if any
