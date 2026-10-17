"""Thumbnails: 320 px on the long side, WEBP, at `<data_dir>/thumbs/<sha256>.webp` (SPEC §6.3).

Images show themselves (upright), PDFs their first page, and .docx/.pptx/.txt/.md a generated card, since
rendering Office files would need LibreOffice. A thumbnail lives exactly as long as its content is indexed.
"""

from __future__ import annotations

import logging
import sqlite3
import textwrap
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from vault.config import Config

log = logging.getLogger(__name__)

SIDE = 320
CARD = (SIDE, 240)
BADGES = {  # extension -> (label, colour)
    ".docx": ("DOCX", (43, 87, 154)),
    ".pptx": ("PPTX", (196, 79, 41)),
    ".txt": ("TXT", (90, 98, 110)),
    ".md": ("MD", (110, 66, 160)),
}


def thumbs_dir(cfg: Config) -> Path:
    return cfg.data_dir / "thumbs"


def rel_path(sha: str) -> str:
    """What `files.thumb_path` stores: relative to the data dir, so an index can move with its thumbnails."""
    return f"thumbs/{sha}.webp"


def image_thumb(img: Image.Image) -> Image.Image:
    out = img.convert("RGB")
    out.thumbnail((SIDE, SIDE), Image.Resampling.LANCZOS)
    return out


def pdf_thumb(path: Path) -> Image.Image:
    """Page 1, rendered so its long side is 320 px."""
    with pymupdf.open(path) as doc:
        page = doc[0]
        zoom = SIDE / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def card_thumb(ext: str, title: str) -> Image.Image:
    """A file-type badge and the first words of the file."""
    label, colour = BADGES.get(ext, (ext.lstrip(".").upper() or "FILE", (90, 98, 110)))
    img = Image.new("RGB", CARD, (246, 247, 249))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, CARD[0] - 1, CARD[1] - 1), outline=(214, 218, 224))
    badge = ImageFont.load_default(size=18)
    w = d.textlength(label, font=badge)
    d.rounded_rectangle((16, 16, 16 + w + 20, 46), 6, fill=colour)
    d.text((26, 20), label, fill="white", font=badge)
    body = ImageFont.load_default(size=17)
    lines = textwrap.wrap(" ".join(title.split()), width=30)[:7] or ["(empty)"]
    for i, line in enumerate(lines):
        d.text((16, 64 + 23 * i), line, fill=(33, 37, 41), font=body)
    return img


def make_thumb(path: Path, title: str = "", image: Image.Image | None = None) -> Image.Image:
    """The thumbnail for one file. `image` is the already-decoded upright image, for image files."""
    ext = path.suffix.lower()
    if image is not None:
        return image_thumb(image)
    if ext == ".pdf":
        return pdf_thumb(path)
    return card_thumb(ext, title)


def save(img: Image.Image, cfg: Config, sha: str) -> str:
    """Write the thumbnail; return its `thumb_path`."""
    dest = cfg.data_dir / rel_path(sha)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "WEBP", quality=80, method=4)
    return rel_path(sha)


def sweep(conn: sqlite3.Connection, cfg: Config) -> int:
    """Delete thumbnails whose content is no longer indexed (deleted, edited, wiped by reindex, or written
    just before a crash). Returns how many were removed."""
    folder = thumbs_dir(cfg)
    if not folder.is_dir():
        return 0
    live = {r[0] for r in conn.execute("SELECT sha256 FROM files")}
    removed = 0
    for f in folder.glob("*.webp"):
        if f.stem not in live:
            try:
                f.unlink()
                removed += 1
            except OSError as e:  # e.g. open in a viewer on Windows; the next run tries again
                log.warning("could not delete thumbnail %s: %s", f, e)
    return removed
