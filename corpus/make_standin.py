"""Generate the demo corpus's first slice: ~50 generated, non-personal files (SPEC §10, §19 18 Oct).

A stand-in so the M4 deploy isn't blocked on collecting files. Every file is drawn or written here: screenshots
and "photos" are drawn with Pillow's bundled font, PDFs written with PyMuPDF, decks and documents with
python-pptx / python-docx. Organisations, tickets and receipts are fictional. MANIFEST.csv marks every file
`generated`. Before M6 the owner adds real public files and own non-personal photos, and the demo index is
rebuilt (`python -m vault build-demo`).

    python corpus/make_standin.py          # rewrites the generated files in place, and MANIFEST.csv
"""

from __future__ import annotations

import csv
import io
import os
import random
import sys
from datetime import datetime
from pathlib import Path

import docx
import numpy as np
import pptx
import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "bench"))

import scenes  # noqa: E402  (bench/scenes.py: the drawn scenes the CLIP calibrations use)

MANIFEST: list[dict[str, str]] = []


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)  # Pillow's bundled font: the same pixels on every OS


def record(path: Path, kind: str, mtime: datetime) -> None:
    ts = mtime.timestamp()
    os.utime(path, (ts, ts))  # a fixed mtime, so files dated by mtime get the same date on every rebuild
    MANIFEST.append({"path": path.relative_to(HERE.parent).as_posix(), "kind": kind,
                     "source_url_or_own": "generated (corpus/make_standin.py)",
                     "licence": "generated for this project; no third-party content"})


def wrap(d: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            trial = f"{line} {word}".strip()
            if d.textlength(trial, font=f) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- screen captures


def desktop(size=(1920, 1080)) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    w, h = size
    grad = Image.linear_gradient("L").resize((w, h))
    img = Image.composite(Image.new("RGB", size, (18, 52, 86)), Image.new("RGB", size, (58, 110, 150)), grad)
    d = ImageDraw.Draw(img)
    d.rectangle((0, h - 48, w, h), fill=(32, 32, 36))
    for i in range(6):
        d.rounded_rectangle((16 + i * 52, h - 40, 52 + i * 52, h - 8), 6, fill=(70, 70, 78))
    d.text((w - 90, h - 36), "10:15", fill="white", font=font(18))
    return img, d


def window(d: ImageDraw.ImageDraw, box, title: str, body=(255, 255, 255), bar=(243, 243, 243)) -> tuple[int, int]:
    x0, y0, x1, y1 = box
    d.rectangle((x0 + 6, y0 + 6, x1 + 6, y1 + 6), fill=(0, 0, 0))  # shadow
    d.rectangle(box, fill=body, outline=(160, 160, 160))
    d.rectangle((x0, y0, x1, y0 + 36), fill=bar)
    d.text((x0 + 14, y0 + 9), title, fill=(30, 30, 30), font=font(17))
    bx = x1 - 30  # close, maximise, minimise, drawn (the bundled font has no box glyph)
    d.line((bx, y0 + 12, bx + 12, y0 + 24), fill=(60, 60, 60), width=2)
    d.line((bx, y0 + 24, bx + 12, y0 + 12), fill=(60, 60, 60), width=2)
    d.rectangle((bx - 46, y0 + 12, bx - 34, y0 + 24), outline=(60, 60, 60), width=2)
    d.line((bx - 92, y0 + 18, bx - 80, y0 + 18), fill=(60, 60, 60), width=2)
    return x0 + 24, y0 + 56


def error_dialog(title: str, heading: str, message: str, buttons=("OK",), behind: str = "Documents") -> Image.Image:
    img, d = desktop()
    window(d, (180, 120, 1500, 860), behind)
    for i in range(10):
        d.rectangle((220, 200 + 56 * i, 1440, 236 + 56 * i), fill=(246, 246, 248))
    x, y = window(d, (560, 330, 1360, 650), title)
    d.ellipse((x, y, x + 48, y + 48), fill=(200, 40, 40))
    d.text((x + 15, y + 6), "x", fill="white", font=font(30))
    d.text((x + 72, y), heading, fill=(20, 20, 20), font=font(24))
    for i, line in enumerate(wrap(d, message, font(19), 650)):
        d.text((x + 72, y + 44 + 30 * i), line, fill=(40, 40, 40), font=font(19))
    for i, label in enumerate(reversed(buttons)):
        bx = 1330 - 130 * (i + 1)
        d.rounded_rectangle((bx, 590, bx + 110, 630), 4, fill=(0, 103, 192) if i == 0 else (225, 225, 225))
        d.text((bx + 22, 600), label, fill="white" if i == 0 else "black", font=font(18))
    return img


def terminal(title: str, lines: list[str], size=(1600, 900)) -> Image.Image:
    img = Image.new("RGB", size, (12, 12, 12))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, size[0], 36), fill=(44, 44, 44))
    d.text((14, 9), title, fill=(220, 220, 220), font=font(17))
    for i, line in enumerate(lines):
        bad = line.startswith(("npm ERR!", "CONFLICT", "KeyError", "docker: Error", "fatal", "Automatic merge"))
        colour = (255, 110, 100) if bad else (120, 220, 120) if line.startswith("$") else (225, 225, 225)
        d.text((20, 56 + 30 * i), line, fill=colour, font=font(21))
    return img


def browser(url: str, heading: str, body: str, size=(1600, 1000), banner=None) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w, 40), fill=(222, 225, 230))
    d.rounded_rectangle((12, 6, 300, 40), 8, fill="white")
    d.text((26, 14), heading[:28], fill=(40, 40, 40), font=font(15))
    d.rectangle((0, 40, w, 84), fill="white")
    d.rounded_rectangle((90, 48, w - 90, 78), 15, fill=(241, 243, 244))
    d.text((112, 54), url, fill=(60, 64, 67), font=font(17))
    d.line((0, 84, w, 84), fill=(218, 220, 224))
    y = 140
    if banner:
        d.rectangle((0, 84, w, 84 + 70), fill=banner)
        y = 200
    d.text((140, y), heading, fill=(32, 33, 36), font=font(40))
    for i, line in enumerate(wrap(d, body, font(22), w - 280)):
        d.text((140, y + 80 + 36 * i), line, fill=(60, 64, 67), font=font(22))
    return img


def settings_wifi() -> Image.Image:
    img, d = desktop()
    x, y = window(d, (120, 60, 1800, 1000), "Settings")
    d.rectangle((120, 96, 520, 1000), fill=(243, 243, 243))
    for i, item in enumerate(["System", "Bluetooth & devices", "Network & internet", "Personalisation", "Apps",
                              "Accounts", "Time & language", "Privacy & security"]):
        if i == 2:
            d.rounded_rectangle((136, 150 + 52 * i, 504, 190 + 52 * i), 6, fill=(225, 230, 240))
        d.text((160, 160 + 52 * i), item, fill=(30, 30, 30), font=font(20))
    d.text((580, 130), "Network & internet > Wi-Fi > CampusNet-5G", fill=(20, 20, 20), font=font(30))
    rows = [("Connected, secured", ""), ("Metered connection", "Off"), ("IP assignment", "Automatic (DHCP)"),
            ("IPv4 address", "10.20.31.177"), ("Network band", "5 GHz"), ("Security type", "WPA2-Enterprise"),
            ("Link speed (receive/transmit)", "866/866 Mbps")]
    for i, (k, v) in enumerate(rows):
        d.rounded_rectangle((580, 200 + 78 * i, 1760, 266 + 78 * i), 6, fill=(249, 249, 249), outline=(229, 229, 229))
        d.text((604, 222 + 78 * i), k, fill=(30, 30, 30), font=font(21))
        d.text((1400, 222 + 78 * i), v, fill=(90, 90, 90), font=font(21))
    return img


def expenses_chart() -> Image.Image:
    img, d = desktop()
    window(d, (220, 100, 1700, 960), "Monthly expenses - Spreadsheet")
    d.text((300, 170), "Monthly expenses (Rs)", fill=(20, 20, 20), font=font(32))
    months, values = ["June", "July", "August", "September", "October"], [8200, 9100, 7600, 10400, 6900]
    d.line((320, 860, 1620, 860), fill=(80, 80, 80), width=2)
    for i, (m, v) in enumerate(zip(months, values)):
        x = 380 + 250 * i
        top = 860 - int(v / 11000 * 600)
        d.rectangle((x, top, x + 140, 860), fill=(66, 133, 244))
        d.text((x + 20, top - 34), f"{v:,}", fill=(30, 30, 30), font=font(22))
        d.text((x + 10, 875), m, fill=(30, 30, 30), font=font(22))
    return img


def code_editor() -> Image.Image:
    img = Image.new("RGB", (1600, 900), (30, 30, 30))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1600, 36), fill=(45, 45, 45))
    d.text((20, 9), "train.py", fill=(220, 220, 220), font=font(17))
    code = ["import numpy as np", "", "", "def compute_gradient(w, x, y):",
            '    """Gradient of the mean squared error for y ~ w * x."""', "    error = w * x - y",
            "    return 2 * np.mean(error * x)", "", "", "def train(x, y, lr=0.01, steps=500):", "    w = 0.0",
            "    for step in range(steps):", "        w -= lr * compute_gradient(w, x, y)",
            "        if step % 100 == 0:", '            print(f"step {step}: w = {w:.4f}")', "    return w"]
    for i, line in enumerate(code):
        d.text((14, 56 + 30 * i), f"{i + 1:>3}", fill=(110, 110, 110), font=font(20))
        colour = (86, 156, 214) if line.lstrip().startswith(("def", "import", "return", "for", "if")) else (
            (206, 145, 120) if '"' in line else (212, 212, 212))
        d.text((80, 56 + 30 * i), line, fill=colour, font=font(20))
    return img


def phone_page(app: str, title: str, paragraphs: list[str], height: int, bg="white") -> Image.Image:
    """A tall scrolling phone capture (1080 px wide), text from top to bottom."""
    w = 1080
    img = Image.new("RGB", (w, height), bg)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w, 70), fill=(250, 250, 250))
    d.text((40, 20), "08:12", fill=(20, 20, 20), font=font(28))
    d.text((w - 160, 20), "87%", fill=(20, 20, 20), font=font(28))
    d.rectangle((0, 70, w, 190), fill=(245, 245, 245))
    d.text((40, 105), app, fill=(90, 90, 90), font=font(30))
    y = 240
    for line in wrap(d, title, font(56), w - 100):
        d.text((50, y), line, fill=(20, 20, 20), font=font(56))
        y += 76
    y += 30
    body = font(38)
    for para in paragraphs:
        for line in wrap(d, para, body, w - 100):
            d.text((50, y), line, fill=(40, 40, 40), font=body)
            y += 56
        y += 40
    assert y < height - 60, f"{title}: text needs {y} px"
    return img


def phone_chat(contact: str, messages: list[tuple[bool, str]], height: int) -> Image.Image:
    w = 1080
    img = Image.new("RGB", (w, height), (236, 229, 221))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w, 200), fill=(7, 94, 84))
    d.text((40, 20), "07:58", fill="white", font=font(28))
    d.text((150, 110), contact, fill="white", font=font(44))
    y, f = 260, font(36)
    for mine, text in messages:
        lines = wrap(d, text, f, 640)
        bw = max(d.textlength(line, font=f) for line in lines) + 60
        bh = 52 * len(lines) + 50
        x0 = w - 40 - bw if mine else 40
        d.rounded_rectangle((x0, y, x0 + bw, y + bh), 24, fill=(220, 248, 198) if mine else "white")
        for i, line in enumerate(lines):
            d.text((x0 + 30, y + 24 + 52 * i), line, fill=(20, 20, 20), font=f)
        y += bh + 36
    assert y < height - 160, f"{contact}: chat needs {y} px"
    d.rounded_rectangle((30, height - 130, w - 160, height - 40), 45, fill="white")
    d.ellipse((w - 130, height - 130, w - 40, height - 40), fill=(7, 94, 84))
    return img


def payment_screen(ok: bool, lines: list[str], height: int = 2400) -> Image.Image:
    w = 1080
    img = Image.new("RGB", (w, height), "white")
    d = ImageDraw.Draw(img)
    colour = (22, 150, 80) if ok else (200, 50, 50)
    d.rectangle((0, 0, w, 900), fill=colour)
    d.ellipse((w / 2 - 110, 220, w / 2 + 110, 440), fill="white")
    if ok:  # a drawn tick: the bundled font has no check-mark glyph
        d.line((w / 2 - 55, 335, w / 2 - 15, 380, w / 2 + 60, 280), fill=colour, width=22, joint="curve")
    else:
        d.text((w / 2 - 16, 270), "!", fill=colour, font=font(110))
    d.text((80, 520), lines[0], fill="white", font=font(64))
    d.text((80, 640), lines[1], fill="white", font=font(88))
    for i, line in enumerate(lines[2:]):
        d.text((80, 1000 + 90 * i), line, fill=(40, 40, 40), font=font(40))
    return img


# ---------------------------------------------------------------- "photos"


def photoish(img: Image.Image, seed: int, size=(1600, 1200)) -> Image.Image:
    """A drawn picture made to look a little more like a camera photo: upscaled, softened, sensor noise."""
    img = img.resize(size, Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(1.2))
    arr = np.asarray(img, np.float32) + np.random.default_rng(seed).normal(0, 6, (size[1], size[0], 3))
    yy, xx = np.mgrid[0:size[1], 0:size[0]]
    vignette = 1 - 0.25 * (((xx / size[0] - 0.5) ** 2 + (yy / size[1] - 0.5) ** 2) / 0.5)
    return Image.fromarray(np.clip(arr * vignette[..., None], 0, 255).astype(np.uint8))


def paper_photo(lines: list[str], seed: int, *, ink=(25, 25, 25), paper=(238, 234, 222), wall=None, size_px=64,
                tilt: float = 2.5, board: bool = False) -> Image.Image:
    """A sheet (or a whiteboard) photographed on a wall: large text, a slight tilt, blur and noise."""
    rnd = random.Random(seed)
    w, h = 1600, 1200
    base = np.asarray(scenes.noise_photo(seed, (w, h)), np.float32) * 0.5 + (wall or 60)
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    sheet = Image.new("RGB", (1200 if board else 1000, 860 if board else 820), (246, 247, 248) if board else paper)
    d = ImageDraw.Draw(sheet)
    if board:
        d.rectangle((0, 0, sheet.width - 1, sheet.height - 1), outline=(150, 150, 155), width=14)
    for i, line in enumerate(lines):
        d.text((70, 70 + int(size_px * 1.6) * i), line, fill=ink, font=font(size_px))
    sheet = sheet.rotate(tilt + rnd.uniform(-0.5, 0.5), expand=True, fillcolor=tuple(int(x) for x in base[0, 0]))
    img.paste(sheet, ((w - sheet.width) // 2, (h - sheet.height) // 2))
    return photoish(img, seed, (w, h))


def save_photo(img: Image.Image, path: Path, taken: datetime) -> None:
    """A JPEG with camera EXIF (so it classifies as a photo) and no GPS."""
    exif = Image.Exif()
    exif[0x010F] = "StandIn"  # Make
    exif[0x0110] = "Generated Camera"  # Model
    exif.get_ifd(0x8769)[0x9003] = taken.strftime("%Y:%m:%d %H:%M:%S")  # DateTimeOriginal
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=88, exif=exif)
    record(path, "photo", taken)


def save_screenshot(img: Image.Image, path: Path, when: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    record(path, "screenshot", when)


# ---------------------------------------------------------------- documents


def text_pdf(path: Path, title: str, pages: list[str], created: datetime) -> None:
    doc = pymupdf.open()
    doc.set_metadata({"title": title, "creationDate": created.strftime("D:%Y%m%d%H%M%S"),
                      "producer": "make_standin.py"})
    for i, text in enumerate(pages):
        page = doc.new_page()
        top = 60
        if i == 0:
            page.insert_textbox(pymupdf.Rect(60, 60, 540, 140), title, fontsize=20, fontname="helv")
            top = 140
        page.insert_textbox(pymupdf.Rect(60, top, 540, 790), text, fontsize=11.5, fontname="helv")
        page.insert_text((290, 815), str(i + 1), fontsize=9)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    record(path, "pdf", created)


def slide_image(lines: list[str], size=(1500, 700), bg=(250, 250, 252), ink=(30, 30, 40)) -> Image.Image:
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    y = 60
    for line in lines:
        for part in wrap(d, line, font(52), size[0] - 140):
            d.text((70, y), part, fill=ink, font=font(52))
            y += 74
        y += 30
    return img


def slides_pdf(path: Path, slides: list[tuple[str, list[str] | None]], created: datetime) -> None:
    """Slides exported to PDF: native title text, and a body that's a picture (its text only OCR finds)."""
    doc = pymupdf.open()
    doc.set_metadata({"creationDate": created.strftime("D:%Y%m%d%H%M%S"), "producer": "make_standin.py"})
    for title, body in slides:
        page = doc.new_page(width=960, height=540)
        page.insert_text((60, 80), title, fontsize=30, fontname="helv")
        if body:
            page.insert_image(pymupdf.Rect(60, 120, 900, 512), stream=png_bytes(slide_image(body)))
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    record(path, "pdf", created)


def scanned_page(lines: list[str], seed: int, size=(1240, 1754)) -> Image.Image:
    """A page of a printed book, scanned: greyish paper, slight tilt and noise, no text layer."""
    img = Image.new("RGB", size, (243, 241, 236))
    d = ImageDraw.Draw(img)
    y = 140
    for line in lines:
        if line.startswith("# "):
            d.text((130, y), line[2:], fill=(15, 15, 15), font=font(48))
            y += 90
            continue
        for part in wrap(d, line, font(30), size[0] - 260) if line else [""]:
            d.text((130, y), part, fill=(25, 25, 25), font=font(30))
            y += 46
    img = img.rotate(random.Random(seed).uniform(-0.8, 0.8), fillcolor=(243, 241, 236))
    arr = np.asarray(img, np.float32) + np.random.default_rng(seed).normal(0, 7, (size[1], size[0], 3))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))


def scanned_pdf(path: Path, pages: list[Image.Image], created: datetime) -> None:
    doc = pymupdf.open()
    doc.set_metadata({"creationDate": created.strftime("D:%Y%m%d%H%M%S"), "producer": "make_standin.py"})
    for img in pages:
        page = doc.new_page()
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        page.insert_image(page.rect, stream=buf.getvalue())
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    record(path, "pdf", created)


def deck(path: Path, slides: list[tuple[str, str, str]], created: datetime) -> None:
    prs = pptx.Presentation()
    prs.core_properties.created = created
    prs.core_properties.modified = created
    prs.core_properties.author = "Personal Search Vault (generated)"
    for title, body, notes in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(path))
    record(path, "slides", created)


def word_doc(path: Path, heading: str, paragraphs: list[str], created: datetime,
             table: list[list[str]] | None = None) -> None:
    d = docx.Document()
    d.core_properties.created = created
    d.core_properties.modified = created
    d.core_properties.author = "Personal Search Vault (generated)"
    d.add_heading(heading, level=1)
    for p in paragraphs:
        d.add_paragraph(p)
    if table:
        t = d.add_table(rows=len(table), cols=len(table[0]))
        t.style = "Table Grid"
        for r, row in enumerate(table):
            for c, val in enumerate(row):
                t.cell(r, c).text = val
    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(path))
    record(path, "doc", created)


def text_file(path: Path, text: str, when: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")
    record(path, "doc", when)


# ---------------------------------------------------------------- the slice


def screens(out: Path) -> None:
    s = out / "screenshots"
    save_screenshot(error_dialog("Installer", "Setup could not complete",
                                 "Error 0x80070005: Access is denied. Run the installer as an administrator "
                                 "and try again.", ("Retry", "Cancel")),
                    s / "Screenshot 2026-10-12 101530.png", datetime(2026, 10, 12, 10, 15, 30))
    save_screenshot(terminal("Terminal - users", [
        "$ python manage_users.py --sync", "Traceback (most recent call last):",
        '  File "manage_users.py", line 42, in <module>', "    sync_users(load_config())",
        '  File "manage_users.py", line 27, in sync_users', '    uid = record["user_id"]',
        "KeyError: 'user_id'", "$"]), s / "Screenshot 2026-10-13 091204.png", datetime(2026, 10, 13, 9, 12, 4))
    save_screenshot(terminal("Terminal - dashboard", [
        "$ npm install", "npm ERR! code ERESOLVE", "npm ERR! ERESOLVE unable to resolve dependency tree",
        "npm ERR! While resolving: dashboard@0.4.1", "npm ERR! Found: react@18.3.1",
        'npm ERR! Could not resolve dependency: peer react@"^17.0.0" from chart-widgets@2.2.0',
        "npm ERR! Fix the upstream dependency conflict, or retry with --legacy-peer-deps", "$"]),
        s / "Screenshot 2026-10-13 151022.png", datetime(2026, 10, 13, 15, 10, 22))
    save_screenshot(terminal("Terminal - webapp", [
        "$ git merge feature/login", "Auto-merging src/auth/session.py",
        "CONFLICT (content): Merge conflict in src/auth/session.py",
        "Automatic merge failed; fix conflicts and then commit the result.", "$ git status --short",
        "UU src/auth/session.py", "$"]), s / "Screenshot 2026-10-14 112233.png", datetime(2026, 10, 14, 11, 22, 33))
    save_screenshot(browser("http://localhost:8000/docs/setup", "404 Not Found",
                            "The requested URL /docs/setup was not found on this server. Check the address, or go "
                            "back to the documentation index."),
                    s / "Screenshot 2026-10-14 180501.png", datetime(2026, 10, 14, 18, 5, 1))
    save_screenshot(error_dialog("Storage", "Low Disk Space",
                                 "You are running out of disk space on Local Disk (C:). 1.2 GB free of 237 GB. "
                                 "Free up space by removing temporary files.", ("Clean up", "Ignore"), "Downloads"),
                    s / "Screenshot 2026-10-15 083015.png", datetime(2026, 10, 15, 8, 30, 15))
    save_screenshot(settings_wifi(), s / "Screenshot 2026-10-15 120000.png", datetime(2026, 10, 15, 12, 0, 0))
    save_screenshot(expenses_chart(), s / "Screenshot 2026-10-16 094512.png", datetime(2026, 10, 16, 9, 45, 12))
    save_screenshot(code_editor(), s / "Screenshot 2026-10-16 201010.png", datetime(2026, 10, 16, 20, 10, 10))
    save_screenshot(browser("https://intranet.example.edu/timetable", "Your connection is not private",
                            "Attackers might be trying to steal your information from intranet.example.edu (for "
                            "example, passwords, messages or credit cards). NET::ERR_CERT_DATE_INVALID",
                            banner=(217, 48, 37)), s / "Screenshot (3).png", datetime(2026, 10, 9, 14, 0, 0))
    save_screenshot(terminal("Terminal - compose", [
        "$ docker run -p 8080:80 --name web nginx:1.27",
        "docker: Error response from daemon: driver failed programming external connectivity on endpoint web:",
        "Bind for 0.0.0.0:8080 failed: port is already allocated.", "$ docker ps --format '{{.Names}} {{.Ports}}'",
        "old-web 0.0.0.0:8080->80/tcp", "$"]), s / "Screenshot (4).png", datetime(2026, 10, 10, 16, 30, 0))
    save_screenshot(browser("https://shop.example.com/checkout", "Payment failed",
                            "Your card was declined (code 51: insufficient funds). Order A-77310 has not been "
                            "placed. Try another card or pay on delivery.", banner=(255, 236, 179)),
                    s / "Screenshot 2026-10-17 110203.png", datetime(2026, 10, 17, 11, 2, 3))
    save_screenshot(payment_screen(True, ["Payment successful", "Rs 250", "Paid to Campus Canteen",
                                          "UPI transaction ID 629184736251", "18 Oct 2026, 13:05",
                                          "From Savings account ending 4417"]),
                    s / "Screenshot_20261018-130512_Pay.png", datetime(2026, 10, 18, 13, 5, 12))

    t = out / "screenshots" / "phone"
    filler = [
        "Most phones can copy photos, messages and settings to the cloud or to a computer. Pick one place and "
        "check it once a month, because a backup you never test is only a hope.",
        "Photos and videos take the most space. Turn on the option that uploads them only over Wi-Fi, so a long "
        "trip doesn't use up your mobile data.",
        "Chats are usually backed up by the messaging app itself, not by the phone. Open the app's settings and "
        "look for chat backup; choose daily if you can.",
        "Two-factor codes are the hard part. Save the recovery codes for every important account somewhere that "
        "is not the phone you are backing up.",
        "A computer backup is a good second copy. Connect the cable, trust the computer, and copy the camera "
        "folder by hand if nothing else works.",
    ]
    save_screenshot(phone_page("Browser", "How to back up your phone", filler + [
        "Finally, plug the phone in before you go to sleep: the backup finishes overnight while the phone is "
        "charging, and you wake up to a fresh copy."], 4800),
        t / "Screenshot_20261011-221530_Browser.png", datetime(2026, 10, 11, 22, 15, 30))
    save_screenshot(phone_chat("Study group", [
        (False, "Did everyone get the slides for Thursday?"), (True, "Yes, the deck is in the shared folder"),
        (False, "Can we meet before the lab to go over question 3?"), (True, "Library at 4?"),
        (False, "Library is closed on Sunday, try the canteen"), (True, "Canteen works. Bring the printouts"),
        (False, "Who is booking the train tickets for the trip?"), (True, "I booked them last night"),
        (False, "Great, send the PNR here"), (True, "PNR 4521873906, coach B2, berths 34 to 37"),
        (False, "What time do we leave?"), (True, "Meet at platform 4 at 6:40 tomorrow, coach B2"),
    ], 3600), t / "Screenshot_20261012-080102_Messages.png", datetime(2026, 10, 12, 8, 1, 2))
    save_screenshot(phone_page("Notes", "Lab checklist", [
        "Before the session: switch on the fume hood, check the gas valve is closed, and sign the register.",
        "Glassware goes back to the drying rack on the left. Broken glass goes in the yellow box, never the bin.",
        "Label every sample with your name, the date and the batch number. Unlabelled samples are thrown away "
        "on Friday.",
        "Spills: cover with the absorbent pads from the red cabinet, then tell the lab assistant.",
        "Gloves and goggles stay on until you have left the bench area.",
        "Return the pipettes to the stand, tips facing down, and empty the waste beaker into the marked drum.",
        "The spare key for the lab cupboard is in the blue folder on the second shelf.",
    ], 5200), t / "Screenshot_20261012-193344_Notes.png", datetime(2026, 10, 12, 19, 33, 44))
    save_screenshot(phone_page("Reader", "Five tips for better sleep", [
        "Go to bed and get up at the same time every day, weekends included. A regular rhythm matters more than "
        "the number of hours.",
        "Stop caffeine after lunch. Its effect lasts six hours or more, long after you stop feeling it.",
        "Put the phone away half an hour before bed. Reading on paper is fine.",
        "Get daylight in the morning, even ten minutes outside, to set your body clock.",
        "And the room itself: keep the bedroom cool, dark and quiet."], 4200),
        t / "Screenshot_20261013-120000_Reader.png", datetime(2026, 10, 13, 12, 0, 0))
    save_screenshot(phone_page("Mail", "Interview schedule", [
        "Dear candidate, thank you for applying to the summer research internship.",
        "We are pleased to invite you to an interview with the selection panel. Please bring a printed copy of "
        "your CV and your student ID card.",
        "Your interview is scheduled for Thursday 22 October at 11:30 in Room 4B, second floor of the Science "
        "Block.",
        "Reply to this email to confirm, or to ask for another slot."], 3000),
        t / "Screenshot_20261014-090000_Mail.png", datetime(2026, 10, 14, 9, 0, 0))


def photos(out: Path) -> None:
    p = out / "photos"
    for i, (name, draw) in enumerate([
        ("IMG_20260921_183012.jpg", scenes.sunset_beach), ("IMG_20260922_071544.jpg", scenes.green_field),
        ("IMG_20260923_120301.jpg", scenes.snowy_mountains), ("IMG_20260924_221145.jpg", scenes.night_moon),
        ("IMG_20260925_160010.jpg", scenes.dice), ("IMG_20260926_090909.jpg", scenes.red_apple),
        ("IMG_20260927_175500.jpg", scenes.house), ("IMG_20260928_131313.jpg", scenes.rainbow),
        ("IMG_20260929_101010.jpg", scenes.traffic_light), ("IMG_20260930_141414.jpg", scenes.checkerboard),
    ]):
        taken = datetime.strptime(name[4:19], "%Y%m%d_%H%M%S")
        save_photo(photoish(draw(), 100 + i), p / name, taken)

    n = out / "photos" / "notices"
    save_photo(paper_photo(["LIBRARY NOTICE", "The library is closed", "on Sunday 20 October", "for maintenance."], 7),
               n / "IMG_20261002_084500.jpg", datetime(2026, 10, 2, 8, 45))
    save_photo(paper_photo(["Linear regression", "loss = (y - wx - b)^2", "dL/dw = -2x(y - wx - b)",
                            "w = w - lr * dL/dw"], 8, ink=(20, 40, 140), board=True, size_px=60, tilt=-1.5),
               n / "IMG_20261003_103000.jpg", datetime(2026, 10, 3, 10, 30))
    save_photo(paper_photo(["LAB 3 HAS MOVED", "to Room 214,", "second floor"], 9).rotate(180),
               n / "IMG_20261004_150000.jpg", datetime(2026, 10, 4, 15, 0))  # upside down: the angle classifier
    save_photo(paper_photo(["WATER SUPPLY", "will be interrupted", "on 21 October", "from 10 AM to 2 PM"], 10,
                           paper=(250, 245, 200)), n / "IMG_20261005_091500.jpg", datetime(2026, 10, 5, 9, 15))
    save_photo(paper_photo(["BLUE DOOR CAFE  18 Oct 2026 14:32", "Masala dosa        120.00",
                            "Filter coffee x2    90.00", "Paneer roll        160.00", "Lemon soda          65.00",
                            "GST 5%              21.75", "TOTAL Rs 456.75"], 11, paper=(252, 252, 250),
                           size_px=44, tilt=4), n / "IMG_20261018_143500.jpg", datetime(2026, 10, 18, 14, 35))


def pdfs(out: Path) -> None:
    p = out / "papers"
    text_pdf(p / "2006.11239.pdf", "Denoising Diffusion Models: A Short Primer", [
        "Abstract. Diffusion models generate images by learning to reverse a gradual noising process. A forward "
        "process adds a small amount of Gaussian noise to an image over many steps until only noise remains. A "
        "neural network is trained to predict the noise that was added at each step, and sampling runs the chain "
        "backwards: it starts from pure noise and removes a little of it at a time.\n\nThis note explains the "
        "forward process, the training objective and the sampling loop, and compares diffusion models with "
        "generative adversarial networks and variational autoencoders.",
        "1. The forward process\nAt step t the image x_t is a weighted mix of the previous image and fresh "
        "Gaussian noise. The noise schedule beta_1 ... beta_T controls how quickly information is destroyed. "
        "Because a sum of Gaussians is Gaussian, x_t can be sampled from the clean image x_0 in a single step.\n\n"
        "2. Training\nThe network sees a noisy image and the step number, and predicts the noise. The loss is the "
        "mean squared error between the true noise and the predicted noise, averaged over random steps.",
        "3. Sampling\nStart from x_T drawn from a standard normal distribution. For t = T down to 1, subtract the "
        "predicted noise, rescale, and add a small amount of fresh noise. After T steps the result is a sample from "
        "the learned distribution.\n\n4. Trade-offs\nSampling needs hundreds of network evaluations, so it is "
        "slower than a GAN, but training is stable and the samples cover the data well. Later work reduces the "
        "number of steps with better samplers and distillation.",
    ], datetime(2026, 9, 2, 10, 0))
    text_pdf(p / "1706.03762.pdf", "Attention-Based Sequence Models: Notes", [
        "Abstract. Recurrent networks read a sentence one word at a time, which makes long-range dependencies "
        "hard to learn and training hard to parallelise. Attention lets every position look at every other "
        "position directly. A model built only from attention layers and feed-forward layers trains faster and "
        "translates better than recurrent models of the same size.",
        "1. Scaled dot-product attention\nEach position produces a query, a key and a value. The output for a "
        "position is a weighted average of all values, with weights softmax(Q K^T / sqrt(d)). Dividing by the "
        "square root of the key dimension keeps the softmax from saturating.\n\n2. Multi-head attention\nSeveral "
        "attention heads run in parallel on smaller projections, so each head can attend to a different kind of "
        "relation.",
        "3. Positional information\nAttention ignores word order, so the model adds positional encodings to the "
        "inputs: sine and cosine waves of different frequencies.\n\n4. Cost\nAttention compares every pair of "
        "positions, so its cost grows with the square of the sequence length.",
    ], datetime(2026, 9, 5, 10, 0))
    text_pdf(p / "1412.6980.pdf", "Adaptive Learning Rates: Momentum, RMSProp and Adam", [
        "Plain stochastic gradient descent uses one learning rate for every parameter. Adaptive methods scale "
        "each parameter's step by statistics of its past gradients, which helps when gradients are sparse or "
        "badly scaled.",
        "1. Momentum\nA velocity term accumulates past gradients, so updates keep moving through flat regions and "
        "oscillations across steep valleys cancel out.\n\n2. RMSProp\nEach step is divided by a running average "
        "of squared gradients.\n\n3. Adam\nAdam keeps both a running mean of the gradients (first moment) and of "
        "their squares (second moment), corrects both for their bias towards zero early in training, and steps by "
        "their ratio.",
        "4. Practical defaults\nA learning rate of 0.001 with beta1 = 0.9, beta2 = 0.999 and epsilon = 1e-8 works "
        "for most problems. Warm-up and decay schedules still matter for large models.",
    ], datetime(2026, 9, 8, 10, 0))
    text_pdf(p / "matrix-decompositions.pdf", "Matrix Decompositions: A Field Guide", [
        "This guide collects the matrix factorisations that come up most often in numerical linear algebra and "
        "machine learning: LU, QR, the eigendecomposition and the singular value decomposition, with what each "
        "one is good for.",
        "1. LU decomposition\nA square matrix A can often be written as A = LU, with L lower triangular and U upper "
        "triangular. It is Gaussian elimination written down, and it solves many systems with the same A "
        "quickly.\n\n2. QR decomposition\nAny matrix can be written as A = QR, with Q having orthonormal columns "
        "and R upper triangular. Gram-Schmidt or Householder reflections compute it; it is the stable way to solve "
        "least-squares problems.",
        "3. Eigendecomposition\nA diagonalisable square matrix is A = V Lambda V^-1, with the eigenvalues on the "
        "diagonal of Lambda.\n\n4. Singular value decomposition\nDefinition. Every real m x n matrix A can be "
        "written as A = U Sigma V^T, where U (m x m) and V (n x n) are orthogonal and Sigma is an m x n diagonal "
        "matrix with non-negative entries sigma_1 >= sigma_2 >= ... >= 0, the singular values of A. The columns "
        "of U and V are the left and right singular vectors.",
        "5. Applications\nKeeping the k largest singular values gives the best rank-k approximation of A "
        "(Eckart-Young). The same idea underlies principal component analysis, the pseudo-inverse and simple image "
        "compression.",
    ], datetime(2026, 9, 10, 10, 0))

    s = out / "scans"
    scanned_pdf(s / "scan_0001.pdf", [
        scanned_page(["# Contents", "1  Counting ............................ 1", "2  Conditional probability ......... 9",
                      "3  Bayes' theorem ...................... 17", "4  Random variables .................... 25",
                      "5  Expectation and variance ........ 33", "6  The normal distribution ......... 41"], 21),
        scanned_page(["# 3  Bayes' theorem",
                      "Bayes' theorem relates the probability of a hypothesis given the evidence to the probability "
                      "of the evidence given the hypothesis:", "", "P(H | E) = P(E | H) P(H) / P(E)", "",
                      "Example 3.1. A test for a rare condition is 99 percent accurate, and one person in a thousand "
                      "has the condition. If a person tests positive, the chance that they have it is still under "
                      "ten percent, because false positives from the healthy majority outnumber the true "
                      "positives."], 22),
    ], datetime(2026, 8, 20, 10, 0))
    scanned_pdf(s / "scan_0002.pdf", [scanned_page([
        "# Hostel notice", "Quiet hours are from 10 PM to 6 AM on all floors.",
        "Guests must sign the register at the front desk and leave by 8 PM.",
        "The laundry room closes at 9 PM. Clothes left overnight are moved to the lost and found box.",
        "Report broken fans or lights to the warden's office, room 001."], 23)], datetime(2026, 8, 25, 10, 0))

    d = out / "slides-pdf"
    slides_pdf(d / "lecture-slides-cnn.pdf", [
        ("Convolutional networks", ["Week 6: images as grids of numbers"]),
        ("Convolution", ["A convolution slides a small kernel across the image",
                         "and sums the products at every position."]),
        ("Pooling", ["Max pooling keeps the largest value in each 2 x 2 window,",
                     "halving the width and height of the feature map."]),
        ("Summary", ["Stack convolution, a nonlinearity and pooling,", "then finish with a classifier."]),
    ], datetime(2026, 9, 15, 10, 0))
    slides_pdf(d / "slides-export-rl.pdf", [
        ("Reinforcement learning", ["The agent maximises the expected discounted return."]),
        ("Markov decision process", ["States, actions, transition probabilities and rewards.",
                                     "The next state depends only on the current state and action."]),
        ("Q-learning", ["Move Q(s, a) towards r + gamma * max Q(s', a')", "after every step."]),
    ], datetime(2026, 9, 18, 10, 0))
    slides_pdf(d / "poster-hackathon.pdf", [
        ("Campus events", ["HACKATHON 2026", "48 hours of building", "Register by 30 October",
                           "Prize pool Rs 50,000"]),
    ], datetime(2026, 10, 1, 10, 0))

    r = out / "receipts"
    text_pdf(r / "ticket-2026-10-24.pdf", "Demo Rail - Electronic reservation slip", [
        "PNR: 4521873906\nTrain: 12345 Coastal Express\nFrom: Bengaluru To: Madgaon\nDate of journey: 24-Oct-2026\n"
        "Departure: 06:40   Arrival: 18:55\nCoach: B2   Berths: 34, 35, 36, 37 (Lower, Middle, Upper, Side lower)\n"
        "Passengers: 4   Fare: Rs 3,180.00\n\nCarry a photo ID. This is a generated sample ticket.",
    ], datetime(2026, 10, 11, 21, 40))
    text_pdf(r / "electricity-bill-october.pdf", "Example Power Co. - Electricity bill, October 2026", [
        "Consumer number: 110045227\nBilling period: 01-Sep-2026 to 30-Sep-2026\nUnits consumed: 212 kWh\n"
        "Energy charges: Rs 1,102.40\nFixed charges: Rs 120.00\nTax: Rs 62.10\nAmount due: Rs 1,284.50\n"
        "Due date: 05-Nov-2026\n\nPay online or at any collection centre. This is a generated sample bill.",
    ], datetime(2026, 10, 3, 9, 0))


def office(out: Path) -> None:
    k = out / "decks"
    deck(k / "ML lecture v1.pptx", [
        ("Welcome", "Agenda: linear regression, gradient descent, questions", ""),
        ("Linear regression", "Fit a line y = wx + b by minimising the squared error", ""),
        ("Gradient descent", "Update the weights by stepping against the gradient of the loss",
         "The learning rate sets the step size; too large and it diverges"),
        ("Questions", "Office hours: Tuesday 3 to 5 PM", ""),
    ], datetime(2026, 9, 1, 9, 0))
    deck(k / "ML lecture v2.pptx", [
        ("Welcome", "Agenda: loss functions, linear regression, gradient descent with momentum", ""),
        ("Loss functions", "Mean squared error for regression, cross-entropy for classification", ""),
        ("Linear regression", "Fit a line y = wx + b by minimising the squared error", ""),
        ("Gradient descent with momentum",
         "Momentum keeps a running average of past gradients, so updates roll through flat regions and damp "
         "oscillations", "beta = 0.9 is a common default"),
        ("Summary", "Choose the loss for the task; tune the learning rate first", ""),
    ], datetime(2026, 9, 22, 9, 0))
    deck(k / "Linear algebra review.pptx", [
        ("Vectors and norms", "The L2 norm is the square root of the sum of squares", ""),
        ("Matrix multiplication", "Row i of A times column j of B gives entry (i, j) of AB", ""),
        ("Eigenvalues and eigenvectors", "Av = lambda v: the direction of v does not change", ""),
        ("The SVD", "A = U Sigma V^T; the singular values are the square roots of the eigenvalues of A^T A",
         "Every matrix has an SVD, even a non-square one"),
        ("Low-rank approximation", "Keep the k largest singular values to compress A", ""),
    ], datetime(2026, 9, 12, 9, 0))

    n = out / "notes"
    word_doc(n / "meeting notes 2026-10-14.docx", "Project sync, 14 October 2026", [
        "Attendees: Asha, Ravi, Meera and Tom.",
        "Decisions: the demo moves to Friday, and the API is frozen on Wednesday so the front end can catch up.",
        "Risks: the sample PDFs are not collected yet, and the upload limits are not tested on the server.",
    ], datetime(2026, 10, 14, 17, 0), table=[["Action", "Owner", "Due"], ["Write the upload limits", "Ravi", "20 Oct"],
                                             ["Collect sample PDFs", "Meera", "21 Oct"],
                                             ["Book the seminar room", "Asha", "19 Oct"]])
    word_doc(n / "travel plan goa.docx", "Goa trip plan", [
        "Day 1: the Coastal Express leaves Bengaluru at 06:40 from platform 4 and reaches Madgaon at 18:55. "
        "Check in at the guest house near the station.",
        "Day 2: old town walk in the morning, spice farm lunch, ferry back before dark.",
        "Day 3: beach day at Palolem; sunset is around 18:10.",
        "Packing: sunscreen, a hat, a power bank, and printed tickets for all four of us.",
    ], datetime(2026, 10, 8, 20, 0))
    text_file(n / "git cheatsheet.md", """
# Git cheatsheet

- Undo the last commit but keep the changes: `git reset --soft HEAD~1`
- Throw away local changes to one file: `git restore path/to/file`
- See what changed in the last commit: `git show --stat`
- Start a branch from main: `git switch -c feature/name main`
- Resolve a merge conflict: edit the file, remove the markers, then `git add` it and `git commit`
- Find the commit that broke something: `git bisect start`, then mark commits good or bad
""", datetime(2026, 9, 28, 18, 0))
    text_file(n / "reading list.md", """
# Reading list

1. Probability: conditional probability and Bayes' theorem, chapters 2 and 3
2. Linear algebra: the singular value decomposition and low-rank approximation
3. Deep learning: convolutional networks, attention, diffusion models
4. Optimisation: momentum and Adam, learning-rate schedules
5. For fun: a history of the printing press
""", datetime(2026, 9, 30, 21, 0))
    text_file(n / "sourdough recipe.txt", """
Sourdough loaf

Mix 500 g flour, 350 g water and 100 g active starter. Rest 30 minutes, then add 10 g salt.
Stretch and fold four times, every 30 minutes. Let it rise until it has grown by half, 4 to 6 hours.
Shape, put it in a floured basket and proof overnight in the fridge.
Bake in a covered pot at 250 C for 20 minutes, then uncovered at 230 C for 25 minutes.
""", datetime(2026, 10, 6, 9, 0))


def main() -> None:
    screens(HERE)
    photos(HERE)
    pdfs(HERE)
    office(HERE)
    with (HERE / "MANIFEST.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["path", "kind", "source_url_or_own", "licence"])
        w.writeheader()
        w.writerows(sorted(MANIFEST, key=lambda r: r["path"]))
    print(f"{len(MANIFEST)} files written under {HERE}")


if __name__ == "__main__":
    main()
