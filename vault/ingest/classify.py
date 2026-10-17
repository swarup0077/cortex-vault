"""Rule-based file kind (SPEC §6.2). Text formats go by extension; images get an interim rule until M3."""

from __future__ import annotations

from pathlib import Path

EXT_KINDS = {".pdf": "pdf", ".docx": "doc", ".txt": "doc", ".md": "doc", ".pptx": "slides"}
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".heic"})


def classify(path: Path) -> tuple[str, str]:
    """Return `(kind, kind_reason)`."""
    ext = path.suffix.lower()
    if ext in EXT_KINDS:
        return EXT_KINDS[ext], f"ext:{ext}"
    if ext in IMAGE_EXTS:  # interim (SPEC §19, open item i): M3 reclassifies these in place
        return ("screenshot" if ext == ".png" else "photo"), "interim:ext"
    raise ValueError(f"unsupported file type: {path.name}")
