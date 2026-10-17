"""Image text via OCR (SPEC §6.3)."""

from __future__ import annotations

import time
from pathlib import Path

from PIL import Image, ImageOps

from vault import models
from vault.config import Config
from vault.ingest.meta import register_heif
from vault.ingest.ocr import lines_text, ocr_image, reading_order
from vault.ingest.records import Extracted, Segment


def to_rgb(im: Image.Image) -> Image.Image:
    """RGB with any transparency flattened onto white (dark text on a transparent PNG must stay readable)."""
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.getchannel("A"))
        return bg
    return im.convert("RGB")


def load_upright(path: Path) -> Image.Image:
    """First frame, EXIF orientation applied, as RGB."""
    register_heif()
    with Image.open(path) as im:
        return to_rgb(ImageOps.exif_transpose(im))


def clip_input(img: Image.Image, side: int) -> Image.Image:
    """A copy small enough for CLIP: short side at most `side` px (CLIP centre-crops 224 px anyway), so a batch
    of full-size photos doesn't sit in memory."""
    scale = side / min(img.size)
    if scale >= 1:
        return img
    return img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                      Image.Resampling.BICUBIC)


def extract_image(path: Path, cfg: Config, engine: models.OcrEngine | None = None, *, kind: str | None = None,
                  image: Image.Image | None = None) -> Extracted:
    """OCR an image (pass `image` if it's already loaded upright). Screenshots skip the text-angle classifier
    unless `ocr_cls_screenshots` is set: on-screen text is never upside down."""
    img = image if image is not None else load_upright(path)
    use_cls = cfg.ocr_cls_screenshots if kind == "screenshot" else None
    t0 = time.perf_counter()
    lines = reading_order(ocr_image(img, engine or models.ocr_engine(cfg), cfg, use_cls=use_cls), cfg)
    seconds = time.perf_counter() - t0
    segments = [Segment(lines_text(lines), None, "ocr", tuple(lines))] if lines else []
    return Extracted(segments=segments, width=img.width, height=img.height, ocr_seconds=seconds, ocr_images=1,
                     ocr_empty=0 if lines else 1)
