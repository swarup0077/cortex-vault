"""Generate the synthetic ingest-speed set of SPEC §19 (deterministic: fixed seeds, Pillow's bundled font).

    python bench/make_bench_corpus.py [out_dir]      # default: bench/generated/corpus (gitignored)

- 20 screenshots, 1920x1080 PNG (UI-like: title bar, sidebar, 13-16 px body text, an error dialog)
- 5 phone scrolling screenshots, 1080x2400 PNG, and 2 tall ones, 1080x6000 PNG
- 10 photos, 4000x3000 JPEG (gradients and shapes; 3 with a sign of large text)
- 5 scanned PDFs, 3 pages each: letter-size page images at 150 DPI (24-28 px text), no text layer
- 5 slide-style PDFs, 4 pages each: native title + a picture body with text on 2 of the pages
"""

from __future__ import annotations

import io
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pymupdf  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from bench_ocr import font, screenshot, sentence  # noqa: E402


def phone(seed: int, height: int) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", (1080, height), "white")
    d = ImageDraw.Draw(img)
    y = 40
    while y < height - 120:
        bubble_left = rnd.random() < 0.5
        x = 40 if bubble_left else 380
        d.rounded_rectangle((x - 16, y - 12, x + 660, y + 92), 24, fill=(232, 240, 254) if bubble_left else (220, 248, 198))
        d.text((x, y), sentence(rnd, 6), fill="black", font=font(30))
        d.text((x, y + 44), sentence(rnd, 5), fill="black", font=font(30))
        y += 150
    return img


def photo(seed: int, sign: bool) -> Image.Image:
    rnd = random.Random(seed)
    top, bottom = [tuple(rnd.randrange(256) for _ in range(3)) for _ in range(2)]
    grad = Image.linear_gradient("L").resize((4000, 3000))
    img = Image.composite(Image.new("RGB", (4000, 3000), top), Image.new("RGB", (4000, 3000), bottom), grad)
    d = ImageDraw.Draw(img)
    for _ in range(12):
        x, y, r = rnd.randrange(4000), rnd.randrange(3000), rnd.randrange(60, 500)
        d.ellipse((x - r, y - r, x + r, y + r), fill=tuple(rnd.randrange(256) for _ in range(3)))
    if sign:
        d.rectangle((1200, 900, 2800, 1500), fill="white", outline="black", width=12)
        d.text((1300, 1000), sentence(rnd, 2).upper(), fill="black", font=font(160))
        d.text((1300, 1250), sentence(rnd, 3), fill="black", font=font(110))
    return img


def page_image(seed: int) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", (1275, 1650), (250, 250, 247))  # letter at 150 DPI
    d = ImageDraw.Draw(img)
    d.text((120, 110), sentence(rnd, 5), fill="black", font=font(30))
    y = 190
    while y < 1500:
        for _ in range(rnd.randint(4, 8)):
            d.text((120, y), sentence(rnd, 14), fill=(20, 20, 20), font=font(rnd.choice([24, 26, 28])))
            y += 40
        y += 30
    return img


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def main(out: Path) -> None:
    (out / "screens").mkdir(parents=True, exist_ok=True)
    for i in range(20):
        screenshot(100 + i)[0].save(out / "screens" / f"Screenshot ({i + 1}).png")
    (out / "phone").mkdir(exist_ok=True)
    for i in range(5):
        phone(200 + i, 2400).save(out / "phone" / f"Screenshot_2025101{i}-101530_Chat.png")
    for i in range(2):
        phone(300 + i, 6000).save(out / "phone" / f"Screenshot_2025102{i}-090000_Chat.png")
    (out / "photos").mkdir(exist_ok=True)
    for i in range(10):
        photo(400 + i, sign=i < 3).save(out / "photos" / f"IMG_{2040 + i}.jpg", quality=90)
    (out / "scans").mkdir(exist_ok=True)
    for i in range(5):
        doc = pymupdf.open()
        for p in range(3):
            page = doc.new_page(width=612, height=792)
            page.insert_image(page.rect, stream=png(page_image(500 + 10 * i + p)))
        doc.save(out / "scans" / f"scan {i + 1}.pdf")
    (out / "slides").mkdir(exist_ok=True)
    for i in range(5):
        rnd = random.Random(600 + i)
        doc = pymupdf.open()
        for p in range(4):
            page = doc.new_page(width=720, height=405)
            page.insert_text((40, 50), sentence(rnd, 6), fontsize=22)
            if p in (1, 2):
                body = Image.new("RGB", (1200, 560), "white")
                bd = ImageDraw.Draw(body)
                for k in range(5):
                    bd.text((40, 40 + k * 100), sentence(rnd, 6), fill="black", font=font(44))
                page.insert_image(pymupdf.Rect(40, 80, 680, 380), stream=png(body), keep_proportion=False)
            else:
                page.insert_textbox(pymupdf.Rect(40, 80, 680, 380), " ".join(sentence(rnd, 12) for _ in range(5)), fontsize=14)
        doc.save(out / "slides" / f"deck {i + 1}.pdf")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "generated" / "corpus")
