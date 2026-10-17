"""Records passed between ingest stages."""

from __future__ import annotations

from dataclasses import dataclass, field


class SkipFile(Exception):
    """A file we deliberately don't index (encrypted PDF, zip guard). Recorded as status='skipped'."""


@dataclass(frozen=True)
class OcrLine:
    """One OCR'd line. The box is in original-image pixels (images) or page points (PDFs).

    `block` and `line` are set by reading order: the layout block, and the line's position in reading order.
    """

    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float
    block: int = 0
    line: int = 0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass(frozen=True)
class Segment:
    """A piece of extracted text. `page` is the PDF page or slide (1-based), or None for flowing docs,
    where each segment is one paragraph. OCR segments carry their lines in reading order."""

    text: str
    page: int | None
    source: str  # 'pdf_text' | 'doc_text' | 'slide_text' | 'ocr'
    lines: tuple[OcrLine, ...] = ()


@dataclass
class Extracted:
    segments: list[Segment] = field(default_factory=list)
    pages: int | None = None
    doc_date: str | None = None  # ISO-8601 from document metadata, if any
    width: int | None = None  # images: upright pixels
    height: int | None = None
    ocr_seconds: float = 0.0
    ocr_images: int = 0  # images (or PDF pages) sent to OCR
    ocr_empty: int = 0  # of those, how many gave no text
    ocr_capped: bool = False  # a PDF hit the OCR page cap
