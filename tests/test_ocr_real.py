"""M2 done-when criteria (SPEC §14) with the real RapidOCR engine. The text embedder stays fake: these
tests are about what OCR puts in the index, and BM25 is what finds exact strings."""

from pathlib import Path

import pytest

from conftest import build_slides_pdf, draw_text_image, save_image, text_bbox
from vault.ingest.extract_image import extract_image
from vault.ingest.ocr import blocks
from vault.ingest.pipeline import index_paths
from vault.search.engine import search

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("target", [
    ((700, 160), [(30, 30, "ERROR 0x80070005"), (30, 90, "Access is denied.")], 24),
    # RapidOCR reads this one as "Ox80070005" (checked at 16–28 px): the digit fix has to bring it back.
    ((900, 120), [(20, 40, "Error code: 0x80070005. Access is denied.")], 20),
], ids=["dialog", "sentence-read-as-Ox"])
def test_error_code_screenshot_is_top_hit(conn, cfg, fake_embedder, real_ocr, tmp_path: Path, make_docx,
                                          target) -> None:
    root = tmp_path / "Screenshots"
    save_image(draw_text_image(*target), root / "Screenshot (7).png")
    save_image(draw_text_image((700, 160), [(30, 30, "ERROR 0x80004005"), (30, 90, "Unspecified error")], 24),
               root / "Screenshot (8).png")
    save_image(draw_text_image((700, 160), [(30, 30, "Windows Update"), (30, 90, "Some updates failed")], 24),
               root / "Screenshot (9).png")
    make_docx(root / "notes.docx", ["Common Windows error codes and what they usually mean."])
    rep = index_paths(conn, [root], cfg)
    assert rep.errors == [] and rep.ocr_images == 3

    results = search(conn, "0x80070005", cfg).results
    assert results[0].filename == "Screenshot (7).png" and results[0].ranks["bm25"] == 1
    assert "<mark>0x80070005</mark>" in results[0].snippet_html


def test_tall_screenshot_bottom_text_found_with_its_box(conn, cfg, fake_embedder, real_ocr, tmp_path: Path) -> None:
    """1080×6000 is read as 5 strips (0–1600, 1100–2700, …, 4400–6000)."""
    target, target_xy = "Meeting moved to Thursday afternoon", (60, 5870)
    texts = [(60, y, f"Message {i} about the weekly plans") for i, y in enumerate(range(150, 5700, 450))]
    texts += [
        (60, 1300, "Order confirmed for pickup"),  # wholly inside the overlap of strips 1 and 2: read twice
        (60, 1585, "Parcel left at the front desk"),  # cut by strip 1's bottom edge at 1600
        target_xy + (target,),
    ]
    save_image(draw_text_image((1080, 6000), texts, 32), tmp_path / "chat" / "Screenshot_20251012-101530_Chat.jpg")
    index_paths(conn, [tmp_path / "chat"], cfg)

    r = search(conn, "meeting moved to thursday afternoon", cfg).results[0]
    assert r.filename == "Screenshot_20251012-101530_Chat.jpg" and r.ranks["bm25"] == 1

    def lines_with(word: str):
        return conn.execute("SELECT text, x0, y0, x1, y1 FROM ocr_lines WHERE text LIKE ?", (f"%{word}%",)).fetchall()

    [line] = lines_with("Thursday")
    assert line["text"] == target
    ink = text_bbox(target_xy, target, 32)
    cx, cy = (line["x0"] + line["x1"]) / 2, (line["y0"] + line["y1"]) / 2
    assert abs(cx - (ink[0] + ink[2]) / 2) < 20 and abs(cy - (ink[1] + ink[3]) / 2) < 10
    assert line["x0"] <= ink[0] + 5 and line["x1"] >= ink[2] - 5  # the box covers the drawn text
    # Overlaps: a line read by two strips is stored once, and a line cut by a strip edge is stored whole.
    assert [l["text"] for l in lines_with("pickup")] == ["Order confirmed for pickup"]
    assert [l["text"] for l in lines_with("Parcel")] == ["Parcel left at the front desk"]


def test_two_column_image_gives_two_blocks(cfg, real_ocr, tmp_path: Path) -> None:
    left = ["The left column talks", "about mountain trails", "and early morning hikes", "with plenty of water"]
    right = ["The right column covers", "slow cooked lentil soup", "with garlic and cumin", "served over rice"]
    texts = [(40, 60 + 60 * i, t) for i, t in enumerate(left)] + [(760, 60 + 60 * i, t) for i, t in enumerate(right)]
    path = save_image(draw_text_image((1400, 360), texts, 32), tmp_path / "two columns.png")

    [seg] = extract_image(path, cfg).segments
    got = [[l.text for l in b] for b in blocks(seg.lines)]
    assert got == [left, right]


def test_text_inside_an_image_on_a_pdf_page_is_found(conn, cfg, fake_embedder, real_ocr, tmp_path: Path) -> None:
    picture = draw_text_image((1000, 600), [(40, 150, "Revenue grew 42 percent"), (40, 300, "in the northern region")], 44)
    build_slides_pdf(tmp_path / "deck" / "board review.pdf", [
        ("Quarterly business review for the board", None),
        ("Results by region this quarter", picture),
        ("Questions and next steps for the team", None),
    ])
    rep = index_paths(conn, [tmp_path / "deck"], cfg)
    assert rep.ocr_images == 1

    r = search(conn, "northern region revenue", cfg).results[0]
    assert (r.filename, r.page) == ("board review.pdf", 2)
    assert "<mark>northern</mark> <mark>region</mark>" in r.snippet_html
