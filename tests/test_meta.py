"""Effective dates, one test per source, plus GPS (SPEC §6.3)."""

import os
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from conftest import build_docx, build_pdf, build_pptx, camera_exif, save_image
from vault.ingest.meta import effective_date, filename_date, parse_exif_datetime, read_image_meta
from vault.ingest.pipeline import index_paths

MTIME = 1_760_000_000  # 9 Oct 2025
MTIME_ISO = datetime.fromtimestamp(MTIME).isoformat(timespec="seconds")


@pytest.mark.parametrize("name, iso", [
    ("Screenshot 2026-10-16 170612.png", "2026-10-16T17:06:12"),  # Snipping Tool
    ("Screenshot_20251012-101530_Chrome.jpg", "2025-10-12T10:15:30"),  # Android
    ("Screenshot_20251012_101530_Settings.jpg", "2025-10-12T10:15:30"),  # Samsung
    ("Screen Shot 2025-03-12 at 10.15.30.png", "2025-03-12T10:15:30"),  # macOS
    ("Screenshot 2025-03-12 at 3.04.05 PM.png", "2025-03-12T15:04:05"),  # macOS, 12-hour clock
    ("Screenshot 2025-03-12 at 12.04.05 AM.png", "2025-03-12T00:04:05"),  # newer macOS: U+202F before AM
    ("IMG_20250312_101530.jpg", "2025-03-12T10:15:30"),
    ("IMG_20250312.jpg", "2025-03-12T00:00:00"),
    ("PXL_20250312_101530123.jpg", "2025-03-12T10:15:30"),
    ("IMG-20250312-WA0001.jpg", "2025-03-12T00:00:00"),  # WhatsApp on a phone
    ("VID-20250312-WA0002.mp4", "2025-03-12T00:00:00"),
    ("AUD-20250312-WA0003.opus", "2025-03-12T00:00:00"),
    ("DOC-20250312-WA0004.pdf", "2025-03-12T00:00:00"),
    ("WhatsApp Image 2025-12-23 at 09.28.46.jpeg", "2025-12-23T09:28:46"),  # WhatsApp desktop
    ("WhatsApp Image 2025-12-23 at 09.28.46 (1).jpeg", "2025-12-23T09:28:46"),
])
def test_filename_dates(name, iso) -> None:
    assert filename_date(name) == iso


@pytest.mark.parametrize("name", [
    "Screenshot (12).png", "IMG_2041.jpg", "Screenshot_20251399-101530.jpg", "IMG-20250230-WA0001.jpg",
    "holiday 2025-03-12.jpg", "Screenshot 2025-03-12 at 13.04.05 PM.png",
])
def test_no_filename_date(name) -> None:
    assert filename_date(name) is None


@pytest.mark.parametrize("raw, iso", [
    ("2025:03:12 10:15:30", "2025-03-12T10:15:30"), ("0000:00:00 00:00:00", None), ("", None), (None, None),
    (b"2024:01:02 03:04:05\x00", "2024-01-02T03:04:05"),
])
def test_parse_exif_datetime(raw, iso) -> None:
    assert parse_exif_datetime(raw) == iso


def test_priority() -> None:
    assert effective_date("IMG_20250101_000000.jpg", "2024-06-01T12:00:00", None, MTIME) == (
        "2024-06-01T12:00:00", "exif")
    assert effective_date("DOC-20250312-WA0004.pdf", None, "2020-01-01T00:00:00", MTIME) == (
        "2025-03-12T00:00:00", "filename")
    assert effective_date("report.pdf", None, "2020-01-01T00:00:00", MTIME) == ("2020-01-01T00:00:00", "doc_meta")
    assert effective_date("report.pdf", None, None, MTIME) == (MTIME_ISO, "mtime")


def test_exif_date_and_gps_are_read(tmp_path) -> None:
    exif = camera_exif(taken="2025:03:12 10:15:30", gps=((15, 30, 0), "N", (73, 49, 30), "E"))
    meta = read_image_meta(save_image(Image.new("RGB", (64, 48)), tmp_path / "IMG_1.jpg", exif=exif))
    assert meta.taken == "2025-03-12T10:15:30" and meta.gps == (15.5, 73.825) and meta.camera == "TestCam Model 1"


def test_southern_western_gps_is_negative(tmp_path) -> None:
    exif = camera_exif(gps=((33, 52, 4), "S", (151, 12, 36), "W"))
    meta = read_image_meta(save_image(Image.new("RGB", (64, 48)), tmp_path / "a.jpg", exif=exif))
    assert meta.gps == pytest.approx((-33.867778, -151.21))


# ---------------------------------------------------------------- one test per source, through the pipeline


def indexed_dates(conn, cfg, root: Path) -> dict[str, tuple]:
    for p in root.rglob("*"):
        if p.is_file():
            os.utime(p, (MTIME, MTIME))
    index_paths(conn, [root], cfg)
    return {Path(r[0]).name: tuple(r[1:]) for r in conn.execute(
        "SELECT p.path, f.effective_date, f.date_source, f.gps_lat, f.gps_lon FROM files f "
        "JOIN paths p ON p.file_id = f.id")}


def test_source_exif(conn, cfg, fake_embedder, tmp_path) -> None:
    exif = camera_exif(taken="2025:03:12 10:15:30", gps=((15, 30, 0), "N", (73, 49, 30), "E"))
    save_image(Image.new("RGB", (64, 48)), tmp_path / "c" / "IMG_20240101_000000.jpg", exif=exif)
    assert indexed_dates(conn, cfg, tmp_path / "c") == {
        "IMG_20240101_000000.jpg": ("2025-03-12T10:15:30", "exif", 15.5, 73.825)}  # EXIF beats the name


def test_source_filename(conn, cfg, fake_embedder, tmp_path) -> None:
    save_image(Image.new("RGB", (64, 48)), tmp_path / "c" / "WhatsApp Image 2025-12-23 at 09.28.46.jpeg")
    build_pdf(tmp_path / "c" / "DOC-20250312-WA0004.pdf", ["ticket"], created="D:20200101000000")
    assert indexed_dates(conn, cfg, tmp_path / "c") == {
        "WhatsApp Image 2025-12-23 at 09.28.46.jpeg": ("2025-12-23T09:28:46", "filename", None, None),
        "DOC-20250312-WA0004.pdf": ("2025-03-12T00:00:00", "filename", None, None),  # the name beats doc_meta
    }


def test_source_doc_meta(conn, cfg, fake_embedder, tmp_path) -> None:
    """Office `created` is set explicitly: the python-docx/pptx templates carry a 2013 date (open item c)."""
    build_pdf(tmp_path / "c" / "paper.pdf", ["text"], created="D:20250312101530+05'30'")
    build_docx(tmp_path / "c" / "notes.docx", ["text"], created=datetime(2024, 5, 6, 7, 8, 9))
    build_pptx(tmp_path / "c" / "deck.pptx", [("Title", "body", "")], created=datetime(2023, 4, 5, 6, 7, 8))
    assert {k: v[:2] for k, v in indexed_dates(conn, cfg, tmp_path / "c").items()} == {
        "paper.pdf": ("2025-03-12T10:15:30", "doc_meta"),
        "notes.docx": ("2024-05-06T07:08:09", "doc_meta"),
        "deck.pptx": ("2023-04-05T06:07:08", "doc_meta"),
    }


def test_source_mtime(conn, cfg, fake_embedder, tmp_path) -> None:
    save_image(Image.new("RGB", (64, 48)), tmp_path / "c" / "Screenshot (12).png")
    (tmp_path / "c" / "todo.txt").write_text("milk", encoding="utf-8")
    want = (MTIME_ISO, "mtime", None, None)
    assert indexed_dates(conn, cfg, tmp_path / "c") == {"Screenshot (12).png": want, "todo.txt": want}
