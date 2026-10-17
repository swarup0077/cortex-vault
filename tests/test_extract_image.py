"""Image loading and OCR routing with the fake engine (SPEC §6.3)."""

from pathlib import Path

from PIL import Image

from conftest import draw_text_image, save_image
from vault.config import Config
from vault.ingest.extract_image import extract_image, load_upright
from vault.ingest.records import OcrLine

CFG = Config()


def test_exif_orientation_is_applied(tmp_path: Path) -> None:
    upright = draw_text_image((600, 200), [(20, 80, "upright")])
    stored = upright.rotate(90, expand=True)  # the camera stored it sideways…
    exif = Image.Exif()
    exif[0x0112] = 6  # …and tagged it "rotate 90° clockwise to display"
    path = save_image(stored, tmp_path / "IMG_0001.jpg", exif=exif)
    img = load_upright(path)
    assert img.size == (600, 200) and img.mode == "RGB"


def test_transparent_png_is_flattened_onto_white(tmp_path: Path) -> None:
    path = save_image(draw_text_image((400, 100), [(10, 30, "dark text")], mode="RGBA"), tmp_path / "t.png")
    img = load_upright(path)
    assert img.getpixel((395, 95)) == (255, 255, 255)
    assert img.getextrema()[0][0] == 0  # the text stayed black


def test_ocr_segment_and_size(tmp_path: Path, fake_ocr) -> None:
    fake_ocr.lines_for = lambda img: [OcrLine("Access is denied", 0.97, 10, 40, 300, 70),
                                      OcrLine("ERROR Ox80070005", 0.95, 10, 5, 320, 35)]
    path = save_image(draw_text_image((800, 100), []), tmp_path / "err.png")
    ex = extract_image(path, CFG)
    assert (ex.width, ex.height, ex.ocr_images, ex.ocr_empty) == (800, 100, 1, 0)
    [seg] = ex.segments
    assert (seg.source, seg.page, seg.text) == ("ocr", None, "ERROR 0x80070005 Access is denied")
    assert [l.text for l in seg.lines] == ["ERROR 0x80070005", "Access is denied"]
    assert [l.line for l in seg.lines] == [0, 1]


def test_wide_image_boxes_map_back_to_original_pixels(tmp_path: Path, fake_ocr) -> None:
    fake_ocr.lines_for = lambda img: [OcrLine("wide", 0.9, 100, 10, 200, 30)]
    path = save_image(Image.new("RGB", (4096, 1000), "white"), tmp_path / "wide.png")
    [line] = extract_image(path, CFG).segments[0].lines
    assert fake_ocr.seen == [(2048, 500)]
    assert (line.x0, line.y0, line.x1, line.y1) == (200, 20, 400, 60)


def test_tall_image_is_read_in_strips(tmp_path: Path, fake_ocr) -> None:
    path = save_image(Image.new("RGB", (1080, 6000), "white"), tmp_path / "Screenshot_20251012-101530_Chrome.jpg")
    ex = extract_image(path, CFG)
    assert fake_ocr.seen == [(1080, 1600)] * 5
    assert ex.segments == [] and (ex.width, ex.height) == (1080, 6000)
    assert (ex.ocr_images, ex.ocr_empty) == (1, 1)
