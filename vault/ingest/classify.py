"""Rule-based file kind (SPEC §6.2). Text formats go by extension; images by name, EXIF and size, in order."""

from __future__ import annotations

import re
from pathlib import Path

from vault.ingest.meta import ImageMeta, read_image_meta

EXT_KINDS = {".pdf": "pdf", ".docx": "doc", ".txt": "doc", ".md": "doc", ".pptx": "slides"}
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".heic"})

SCREENSHOT_NAMES = [  # rule 1
    ("snipping-tool", re.compile(r"^Screenshot \d{4}-\d{2}-\d{2} \d{6}")),  # Screenshot 2026-10-16 170612.png
    ("prtscn", re.compile(r"^Screenshot \(\d+\)")),  # Win+PrtScn: Screenshot (12).png
    ("android", re.compile(r"^Screenshot_\d{8}[-_]\d{6}")),  # Screenshot_20251012-101530_Chrome.jpg
    ("macos", re.compile(r"^Screen ?[Ss]hot \d{4}-\d{2}-\d{2} at ")),  # Screen Shot / Screenshot 2025-03-12 at …
    ("generic", re.compile(r"^(?:scr_|screencapture)", re.IGNORECASE)),
]

# Rule 4: common screen resolutions (desktop and phone), matched in either orientation.
SCREEN_SIZES = frozenset({
    (1280, 720), (1280, 800), (1366, 768), (1440, 900), (1536, 864), (1600, 900), (1680, 1050), (1920, 1080),
    (1920, 1200), (2256, 1504), (2560, 1440), (2560, 1600), (2880, 1800), (2880, 1920), (3024, 1964),
    (3456, 2234), (3840, 2160),
    (720, 1600), (750, 1334), (828, 1792), (1080, 1920), (1080, 2340), (1080, 2400), (1125, 2436), (1170, 2532),
    (1179, 2556), (1242, 2688), (1284, 2778), (1290, 2796), (1440, 3088), (1440, 3200),
})
# Rule 5: phone screen widths; a scrolling screenshot is at least twice as tall as wide.
PHONE_WIDTHS = frozenset({720, 1080, 1170, 1179, 1242, 1284, 1440})


def classify(path: Path, meta: ImageMeta | None = None) -> tuple[str, str]:
    """Return `(kind, kind_reason)`. For images, pass `meta` if it's already read; otherwise it's read here."""
    ext = path.suffix.lower()
    if ext in EXT_KINDS:
        return EXT_KINDS[ext], f"ext:{ext}"
    if ext not in IMAGE_EXTS:
        raise ValueError(f"unsupported file type: {path.name}")
    return classify_image(path.name, meta if meta is not None else read_image_meta(path))


def classify_image(name: str, meta: ImageMeta | None) -> tuple[str, str]:
    """The §6.2 image rules in order. `meta` is None for an unreadable image (name and extension rules only)."""
    for label, pat in SCREENSHOT_NAMES:
        if pat.match(name):
            return "screenshot", f"filename:{label}"
    if meta is not None and meta.camera:
        return "photo", "exif:camera"
    if name.lower().endswith(".png"):
        return "screenshot", "png-no-exif"
    if meta is None:
        return "photo", "default"
    w, h = meta.width, meta.height
    if not meta.has_exif and ((w, h) in SCREEN_SIZES or (h, w) in SCREEN_SIZES):
        return "screenshot", f"screen-size:{w}x{h}"
    if w in PHONE_WIDTHS and h >= 2 * w:
        return "screenshot", f"tall-phone:{w}x{h}"
    return "photo", "default"
