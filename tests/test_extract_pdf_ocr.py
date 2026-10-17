"""PDF scanned-page and image-region OCR with the fake engine (SPEC §6.3)."""

from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from conftest import build_pdf_with_image, build_scanned_pdf, draw_text_image
from vault.config import Config
from vault.ingest.extract_pdf import extract_pdf
from vault.ingest.records import OcrLine

CFG = Config()
A4 = (595, 842)  # PyMuPDF's default page, in points
A4_150DPI = (1240, 1755)


def near(seen: list[tuple[int, int]], *expected: tuple[int, int]) -> bool:
    """Rendered sizes match within a pixel (PyMuPDF rounds pixmap sizes up)."""
    return len(seen) == len(expected) and all(
        abs(a - c) <= 1 and abs(b - d) <= 1 for (a, b), (c, d) in zip(seen, expected)
    )


def blank(size=(800, 600)) -> Image.Image:
    return Image.new("RGB", size, "white")


@pytest.fixture
def one_line(fake_ocr):
    """The fake reads one line at pixels (150, 300)–(450, 330) of whatever it's shown."""
    fake_ocr.lines_for = lambda img: [OcrLine("seen by ocr", 0.9, 150, 300, 450, 330)]
    return fake_ocr


def ocr_segments(ex):
    return [s for s in ex.segments if s.source == "ocr"]


def test_scanned_page_is_ocrd_whole_with_boxes_in_points(tmp_path: Path, one_line) -> None:
    ex = extract_pdf(build_scanned_pdf(tmp_path / "scan.pdf", [blank()]), CFG)
    assert near(one_line.seen, A4_150DPI)
    [seg] = ocr_segments(ex)
    assert (seg.page, seg.text, ex.ocr_images) == (1, "seen by ocr", 1)
    [ln] = seg.lines
    assert (ln.x0, ln.y0, ln.x1, ln.y1) == pytest.approx((72, 144, 216, 158.4), abs=0.2)  # px × 72/150


def test_large_image_region_is_ocrd_and_offset(tmp_path: Path, one_line) -> None:
    path = build_pdf_with_image(tmp_path / "slides.pdf", "Quarterly review of the sales pipeline", blank(), rect=(50, 150, 550, 450))
    ex = extract_pdf(path, CFG)
    assert near(one_line.seen, (1042, 625))  # 500 × 300 pt at 150 DPI
    assert [s.source for s in ex.segments] == ["pdf_text", "ocr"]
    [ln] = ocr_segments(ex)[0].lines
    assert (ln.x0, ln.y0) == pytest.approx((50 + 72, 150 + 144), abs=0.3)


def test_small_image_is_not_ocrd(tmp_path: Path, one_line) -> None:
    path = build_pdf_with_image(tmp_path / "logo.pdf", "Letterhead with a small logo", blank(), rect=(50, 150, 150, 250))
    assert ocr_segments(extract_pdf(path, CFG)) == [] and one_line.seen == []


def test_image_under_native_text_is_not_ocrd(tmp_path: Path, one_line) -> None:
    """A scan with a text layer is already searchable."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=_png(blank()))
    page.insert_text((60, 200), "This scan already has an invisible text layer", fontsize=11, render_mode=3)
    doc.save(tmp_path / "layered.pdf")
    ex = extract_pdf(tmp_path / "layered.pdf", CFG)
    assert one_line.seen == [] and [s.source for s in ex.segments] == ["pdf_text"]


def test_text_only_pages_never_call_ocr(tmp_path: Path, make_pdf, one_line) -> None:
    extract_pdf(make_pdf(tmp_path / "t.pdf", ["A page of ordinary native text, long enough."]), CFG)
    assert one_line.seen == []


def test_ocr_page_cap(tmp_path: Path, one_line) -> None:
    path = build_scanned_pdf(tmp_path / "long scan.pdf", [blank()] * 3)
    ex = extract_pdf(path, replace(CFG, pdf_ocr_max_pages=2))
    assert len(one_line.seen) == 2 and ex.ocr_capped and ex.ocr_images == 2
    assert [s.page for s in ocr_segments(ex)] == [1, 2]


def test_rotated_page_is_read_as_shown_and_boxes_stay_unrotated(tmp_path: Path, one_line) -> None:
    rect = (100, 150, 500, 350)  # 400 × 200 pt, unrotated
    path = build_pdf_with_image(tmp_path / "rot.pdf", "A rotated page with a picture", blank(), rect=rect, rotate=90)
    one_line.lines_for = lambda img: [OcrLine("seen by ocr", 0.9, 50, 300, 350, 330)]  # fits the 417 px width
    ex = extract_pdf(path, CFG)
    assert near(one_line.seen, (417, 833))  # shown rotated: 200 × 400 pt at 150 DPI
    [ln] = ocr_segments(ex)[0].lines
    inside = pymupdf.Rect(rect)
    assert pymupdf.Rect(ln.x0, ln.y0, ln.x1, ln.y1) in inside
    # A wide box in the upright picture is tall in unrotated page space.
    assert (ln.y1 - ln.y0) > (ln.x1 - ln.x0)


def test_short_native_text_is_kept_alongside_ocr(tmp_path: Path, one_line) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=_png(blank()))
    page.insert_text((280, 820), "Page 3", fontsize=9)
    doc.save(tmp_path / "numbered.pdf")
    ex = extract_pdf(tmp_path / "numbered.pdf", CFG)
    assert [s.source for s in ex.segments] == ["pdf_text", "ocr"] and near(one_line.seen, A4_150DPI)


def _png(img: Image.Image) -> bytes:
    from conftest import png_bytes

    return png_bytes(img)


def test_drawn_text_fixture_helper_is_sane() -> None:
    img = draw_text_image((300, 80), [(10, 20, "abc")])
    assert img.size == (300, 80)
