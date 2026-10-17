"""PDF text per page via PyMuPDF (SPEC §6.3). Scanned-page and image-region OCR arrive in M2."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from vault.ingest.records import Extracted, Segment, SkipFile

_PDF_DATE = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?")


def parse_pdf_date(value: str | None) -> str | None:
    """`D:20250312101530+05'30'` → `2025-03-12T10:15:30` (timezone dropped)."""
    m = _PDF_DATE.match(value or "")
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) if g else dflt for g, dflt in zip(m.groups(), (0, 1, 1, 0, 0, 0)))
    if not (1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 61):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{min(s, 59):02d}"


def extract_pdf(path: Path) -> Extracted:
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise SkipFile("encrypted PDF")
        segments = []
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                segments.append(Segment(text=text, page=page.number + 1, source="pdf_text"))
        return Extracted(
            segments=segments,
            pages=doc.page_count,
            doc_date=parse_pdf_date((doc.metadata or {}).get("creationDate")),
        )
