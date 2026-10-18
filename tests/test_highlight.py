"""Highlight boxes (SPEC §7.6, §8): OCR lines and the PDF text layer, in percent of the picture."""

import pymupdf
from PIL import Image

from conftest import build_pdf, build_pdf_with_image, draw_text_image, save_image
from vault.config import Config
from vault.ingest.records import OcrLine
from vault.library import Library
from vault.search import highlight


def line(text, x0, y0, x1, y1):
    return (text, x0, y0, x1, y1)


def test_query_terms_drop_short_words_and_stopwords() -> None:
    assert highlight.query_terms("where is the SVD of a 3x3 matrix defined") == ["svd", "3x3", "matrix", "defin"]


def test_only_lines_with_a_query_word_get_boxes_in_percent() -> None:
    lines = [line("Error 0x80070005: Access is denied.", 192, 432, 768, 459), line("Retry Cancel", 1300, 600, 1500, 640)]
    boxes = highlight.line_boxes(lines, highlight.query_terms("error 0x80070005"), 1920, 1080)
    assert len(boxes) == 1 and boxes[0].kind == "term"
    b = boxes[0]
    assert (round(b.x, 2), round(b.y, 2), round(b.w, 2), round(b.h, 2)) == (10.0, 40.0, 30.0, 2.5)


def test_at_most_ten_boxes() -> None:
    lines = [line(f"error number {i}", 0, i * 10, 100, i * 10 + 8) for i in range(25)]
    assert len(highlight.line_boxes(lines, ["error"], 100, 300)) == highlight.MAX_BOXES


def test_a_meaning_only_match_gets_one_passage_box_around_the_lines() -> None:
    lines = [line("Linear regression", 100, 100, 400, 140), line("w = w - lr * dL/dw", 120, 300, 500, 340)]
    boxes = highlight.line_boxes(lines, highlight.query_terms("whiteboard with equations"), 1000, 1000)
    assert len(boxes) == 1 and boxes[0].kind == "passage"
    assert (boxes[0].x, boxes[0].y, boxes[0].w, boxes[0].h) == (10.0, 10.0, 40.0, 24.0)


def test_image_and_pdf_boxes_through_the_library(tmp_path, fake_embedder, fake_ocr) -> None:
    root = tmp_path / "c"
    save_image(draw_text_image((800, 400), [(40, 300, "ERROR 0x80070005")]), root / "Screenshot (1).png")
    build_pdf(root / "notes.pdf", ["Intro.", "The singular value decomposition of A is U Sigma V transpose."])
    build_pdf_with_image(root / "slide.pdf", "Pooling", draw_text_image((600, 300), [(20, 100, "max pooling")]),
                         rotate=90)

    def lines_for(image):
        h, w = image.shape[:2]
        if w == 800:
            return [OcrLine("ERROR 0x80070005", 0.99, 40.0, 300.0, 400.0, 332.0)]
        return [OcrLine("max pooling", 0.99, 0.2 * w, 0.3 * h, 0.6 * w, 0.4 * h)]

    fake_ocr.lines_for = lines_for
    lib = Library(Config(data_dir=tmp_path / "data"))
    lib.index([root])

    shot = lib.search("0x80070005").results[0]
    data = lib.preview_data(shot.file_id, shot.chunk_id, None, "error 0x80070005")
    assert data["text"] == "ERROR 0x80070005" and data["boxes"] == [
        {"x": 5.0, "y": 75.0, "w": 45.0, "h": 8.0, "kind": "term"}]

    pdf = lib.search("singular value decomposition").results[0]
    boxes = lib.preview_data(pdf.file_id, pdf.chunk_id, 2, "singular value decomposition")["boxes"]
    assert len(boxes) == 3 and all(b["kind"] == "term" and 0 <= b["x"] <= 100 for b in boxes)

    slide = lib.search("max pooling").results[0]
    boxes = lib.preview_data(slide.file_id, slide.chunk_id, 1, "max pooling")["boxes"]
    assert boxes and all(0 <= b["x"] <= 100 and 0 <= b["y"] <= 100 for b in boxes)
    with pymupdf.open(root / "slide.pdf") as doc:  # a rotated page: boxes are on the page as shown (landscape)
        assert doc[0].rect.width > doc[0].rect.height

    assert lib.preview_data(999, None, None, "x") is None
