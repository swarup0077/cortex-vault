"""PDF scanned-page and image-region OCR with the fake engine (SPEC §6.3)."""

from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from conftest import build_pdf_with_image, build_scanned_pdf, draw_text_image
from vault.config import Config
from vault.ingest.extract_pdf import extract_pdf
from vault.ingest.pipeline import index_paths
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


def test_ocrd_pages_without_text_are_counted(tmp_path: Path, fake_ocr) -> None:
    fake_ocr.lines_for = lambda img: [OcrLine("cover text", 0.9, 150, 300, 450, 330)] if len(fake_ocr.seen) == 1 else []
    ex = extract_pdf(build_scanned_pdf(tmp_path / "scan.pdf", [blank()] * 3), CFG)
    assert (ex.ocr_images, ex.ocr_empty) == (3, 2) and [s.page for s in ocr_segments(ex)] == [1]


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


# ---------------------------------------------------------------- early stop for unreadable PDFs (open item k)


def test_stops_early_needs_three_inked_pages_with_little_readable() -> None:
    from vault.ingest.extract_pdf import stops_early
    from vault.ingest.ocr import OcrStats

    cfg = Config()
    scrawl = OcrStats(detected=20, conf_sum=20 * 0.55, kept=12)
    printed = OcrStats(detected=30, conf_sum=30 * 0.97, kept=30)
    blank = OcrStats()
    assert not stops_early([scrawl, scrawl], cfg)  # too early to tell
    assert stops_early([scrawl, scrawl, scrawl], cfg)
    assert not stops_early([blank, blank, scrawl, scrawl], cfg)  # blank pages don't count
    assert not stops_early([printed, scrawl, scrawl], cfg)  # readable text in the probe
    cover = OcrStats(detected=3, conf_sum=3 * 0.99, kept=3)  # a book's sparse front matter, then print: goes on
    assert not stops_early([cover, blank, cover, printed], cfg)
    assert stops_early([cover, blank, cover, scrawl, printed], cfg)  # covers, then unreadable ink: stop


def test_unreadable_scan_stops_after_three_pages_and_says_so(conn, cfg, fake_embedder, fake_ocr, tmp_path) -> None:
    from vault.ingest.records import OcrLine

    pages = [draw_text_image((600, 800), [(40, 100 + 40 * i, "scribble")]) for i in range(8)]
    build_scanned_pdf(tmp_path / "r" / "handwritten notes.pdf", pages)
    fake_ocr.lines_for = lambda img: [OcrLine("x7q", 0.31, 10, 10 + 20 * k, 200, 28 + 20 * k) for k in range(12)] + [
        OcrLine("lemma", 0.62, 10, 300, 120, 318)]
    rep = index_paths(conn, [tmp_path / "r"], cfg)
    assert rep.ocr_images == 3 and len(rep.ocr_stopped) == 1 and rep.ocr_capped == []
    assert "lemma" in " ".join(r[0] for r in conn.execute("SELECT text FROM chunks WHERE source = 'ocr'"))


def test_blank_pages_then_print_never_stop(conn, cfg, fake_embedder, fake_ocr, tmp_path) -> None:
    from vault.ingest.records import OcrLine

    pages = [draw_text_image((600, 800), [])] * 3 + [draw_text_image((600, 800), [(40, 100, "chapter")])] * 4
    build_scanned_pdf(tmp_path / "r" / "book.pdf", pages)
    calls = {"n": 0}

    def lines_for(img):
        calls["n"] += 1
        if calls["n"] <= 3:
            return []  # blank pages: nothing detected
        return [OcrLine(f"Chapter {calls['n']} begins with a clear printed sentence", 0.97, 10, 10, 500, 40)]

    fake_ocr.lines_for = lines_for
    rep = index_paths(conn, [tmp_path / "r"], cfg)
    assert rep.ocr_images == 7 and rep.ocr_stopped == []
