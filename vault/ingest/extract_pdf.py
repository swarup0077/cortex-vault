"""PDF text per page via PyMuPDF, plus OCR of scanned pages and large embedded images (SPEC §6.3).

OCR line boxes are stored in unrotated page points, the same space `page.search_for()` returns, so
highlights from native text and from OCR are drawn the same way.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import replace
from pathlib import Path

import pymupdf
from PIL import Image

from vault import models
from vault.config import Config
from vault.ingest.ocr import lines_text, ocr_image, reading_order
from vault.ingest.records import Extracted, OcrLine, Segment, SkipFile

log = logging.getLogger(__name__)

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


def unrotated_rect(page: pymupdf.Page) -> pymupdf.Rect:
    """The whole page in the unrotated coordinates that text, image boxes and `search_for` use."""
    return page.rect * page.derotation_matrix


def image_regions(page: pymupdf.Page, cfg: Config) -> list[pymupdf.Rect]:
    """Embedded images worth OCR'ing: ≥ `pdf_region_min_frac` of the page, not already covered by native
    text, and not (mostly) inside a region already chosen. Unrotated page coordinates."""
    page_rect = unrotated_rect(page)
    min_area = cfg.pdf_region_min_frac * page_rect.get_area()
    out: list[pymupdf.Rect] = []
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & page_rect
        if r.is_empty or r.get_area() < min_area:
            continue
        if any((r & o).get_area() >= 0.9 * r.get_area() for o in out):
            continue
        if len(page.get_text("text", clip=r).strip()) >= cfg.pdf_region_text_chars:
            continue  # a scan with a text layer is already searchable
        out.append(r)
    return out


def ocr_region(page: pymupdf.Page, rect: pymupdf.Rect, engine: models.OcrEngine, cfg: Config) -> list[OcrLine]:
    """Render `rect` (unrotated points) as the reader sees it, OCR it, and map boxes back to unrotated points."""
    shown = rect * page.rotation_matrix
    pix = page.get_pixmap(dpi=cfg.pdf_ocr_dpi, clip=shown, colorspace=pymupdf.csRGB, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    sx, sy = shown.width / pix.width, shown.height / pix.height
    out = []
    for ln in ocr_image(img, engine, cfg):
        box = pymupdf.Rect(shown.x0 + ln.x0 * sx, shown.y0 + ln.y0 * sy, shown.x0 + ln.x1 * sx, shown.y0 + ln.y1 * sy)
        box = box * page.derotation_matrix
        out.append(replace(ln, x0=box.x0, y0=box.y0, x1=box.x1, y1=box.y1))
    return out


def extract_pdf(path: Path, cfg: Config | None = None, engine: models.OcrEngine | None = None) -> Extracted:
    cfg = cfg or Config()
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise SkipFile("encrypted PDF")
        ex = Extracted(pages=doc.page_count, doc_date=parse_pdf_date((doc.metadata or {}).get("creationDate")))
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                ex.segments.append(Segment(text=text, page=page.number + 1, source="pdf_text"))
            scanned = len(text.strip()) < cfg.pdf_scanned_chars
            regions = [unrotated_rect(page)] if scanned else image_regions(page, cfg)
            if not regions:
                continue
            if ex.ocr_images >= cfg.pdf_ocr_max_pages:
                if not ex.ocr_capped:
                    log.info("OCR cap (%d pages) reached in %s; later pages keep native text only",
                             cfg.pdf_ocr_max_pages, path)
                ex.ocr_capped = True
                continue
            engine = engine or models.ocr_engine(cfg)
            t0 = time.perf_counter()
            lines: list[OcrLine] = []
            for r in regions:  # each region is its own picture: order it alone, keep numbering running
                base = lines[-1].block + 1 if lines else 0
                for ln in reading_order(ocr_region(page, r, engine, cfg), cfg):
                    lines.append(replace(ln, line=len(lines), block=ln.block + base))
            ex.ocr_seconds += time.perf_counter() - t0
            ex.ocr_images += 1
            if lines:
                ex.segments.append(Segment(lines_text(lines), page.number + 1, "ocr", tuple(lines)))
            else:
                ex.ocr_empty += 1
        return ex
