"""Image metadata (EXIF, GPS, upright size) and effective dates (SPEC §6.2, §6.3).

Reads only headers: no pixels are decoded, so the classifier can run before OCR decides how to read an image.
"""

from __future__ import annotations

import functools
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

EXIF_IFD, GPS_IFD = 0x8769, 0x8825
MAKE, MODEL, ORIENTATION, DATETIME_ORIGINAL = 0x010F, 0x0110, 0x0112, 0x9003


@functools.cache
def register_heif() -> None:
    """HEIC support, if the optional pillow-heif is installed."""
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        return
    register_heif_opener()


@dataclass(frozen=True)
class ImageMeta:
    width: int  # upright: EXIF orientation applied
    height: int
    has_exif: bool  # any EXIF tag at all
    camera: str | None  # "make model" when EXIF names a camera
    taken: str | None  # EXIF DateTimeOriginal, ISO-8601
    gps: tuple[float, float] | None  # (lat, lon) in degrees


def _text(value: object) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    text = str(value).strip(" \0") if value is not None else ""
    return text or None


def parse_exif_datetime(value: object) -> str | None:
    """`2025:03:12 10:15:30` → `2025-03-12T10:15:30`; blank or impossible values (`0000:00:00 …`) → None."""
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text[:19], "%Y:%m:%d %H:%M:%S").isoformat(timespec="seconds")
    except ValueError:
        return None


def _degrees(dms: object, ref: object) -> float | None:
    try:
        d, m, s = (float(x) for x in dms)  # type: ignore[union-attr]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    deg = d + m / 60 + s / 3600
    return -deg if _text(ref) in ("S", "W") else deg


def parse_gps(gps: dict) -> tuple[float, float] | None:
    """GPS IFD → (lat, lon), or None when missing or out of range."""
    lat, lon = _degrees(gps.get(2), gps.get(1)), _degrees(gps.get(4), gps.get(3))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat, lon) == (0.0, 0.0):
        return None
    return round(lat, 6), round(lon, 6)


def read_image_meta(path: Path) -> ImageMeta | None:
    """Header-only read; None if the file can't be opened as an image."""
    register_heif()
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            w, h = im.size
            if exif.get(ORIENTATION) in (5, 6, 7, 8):  # stored sideways
                w, h = h, w
            camera = " ".join(t for t in (_text(exif.get(MAKE)), _text(exif.get(MODEL))) if t) or None
            return ImageMeta(
                width=w, height=h, has_exif=len(exif) > 0, camera=camera,
                taken=parse_exif_datetime(exif.get_ifd(EXIF_IFD).get(DATETIME_ORIGINAL)),
                gps=parse_gps(exif.get_ifd(GPS_IFD)),
            )
    except Exception as e:  # corrupt or unsupported: extraction will record the error
        log.debug("no image metadata for %s: %s", path, e)
        return None


# ---------------------------------------------------------------- dates in filenames

_D = r"(?P<y>\d{4})-?(?P<mo>\d{2})-?(?P<d>\d{2})"
_FILENAME_DATES = [
    # Windows Snipping Tool: "Screenshot 2026-10-16 170612"
    re.compile(rf"^Screenshot {_D} (?P<h>\d{{2}})(?P<mi>\d{{2}})(?P<s>\d{{2}})"),
    # macOS: "Screen Shot 2025-03-12 at 10.15.30", "Screenshot 2025-03-12 at 3.04.05 PM" (newer: U+202F before PM)
    re.compile(rf"^Screen ?[Ss]hot {_D} at (?P<h>\d{{1,2}})\.(?P<mi>\d{{2}})\.(?P<s>\d{{2}})(?:[\s ]?(?P<ampm>[AP]M))?"),
    # Android / Samsung: "Screenshot_20251012-101530_Chrome", "Screenshot_20251012_101530"
    re.compile(rf"^Screenshot_{_D}[-_](?P<h>\d{{2}})(?P<mi>\d{{2}})(?P<s>\d{{2}})"),
    # Phone cameras: "IMG_20250312_101530", "PXL_20250312_101530123", or a date only
    re.compile(rf"^(?:IMG|PXL)_{_D}(?:_(?P<h>\d{{2}})(?P<mi>\d{{2}})(?P<s>\d{{2}}))?"),
    # WhatsApp on a phone: "IMG-20250312-WA0001" (also VID-, AUD-, DOC-); date only
    re.compile(rf"^(?:IMG|VID|AUD|DOC)-{_D}-WA\d+"),
    # WhatsApp desktop: "WhatsApp Image 2025-12-23 at 09.28.46"
    re.compile(rf"^WhatsApp Image {_D} at (?P<h>\d{{1,2}})\.(?P<mi>\d{{2}})\.(?P<s>\d{{2}})"),
]


def filename_date(name: str) -> str | None:
    """The date a known filename pattern carries (SPEC §6.3), ISO-8601; None if none matches or it's impossible."""
    for pat in _FILENAME_DATES:
        m = pat.match(name)
        if not m:
            continue
        g = m.groupdict()
        hour = int(g.get("h") or 0)
        if g.get("ampm"):
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if g["ampm"] == "PM" else 0)
        try:
            return datetime(int(g["y"]), int(g["mo"]), int(g["d"]), hour, int(g.get("mi") or 0),
                            int(g.get("s") or 0)).isoformat(timespec="seconds")
        except ValueError:
            return None
    return None


def effective_date(name: str, taken: str | None, doc_date: str | None, mtime: float) -> tuple[str, str]:
    """(ISO date, source): EXIF DateTimeOriginal, then a filename date, then document metadata, then mtime."""
    if taken:
        return taken, "exif"
    if from_name := filename_date(name):
        return from_name, "filename"
    if doc_date:
        return doc_date, "doc_meta"
    return datetime.fromtimestamp(mtime).isoformat(timespec="seconds"), "mtime"
