"""Rule-based file kind (SPEC §6.2). M1 handles text formats by extension; image rules arrive in M3."""

from __future__ import annotations

from pathlib import Path

EXT_KINDS = {".pdf": "pdf", ".docx": "doc", ".txt": "doc", ".md": "doc", ".pptx": "slides"}


def classify(path: Path) -> tuple[str, str]:
    """Return `(kind, kind_reason)`."""
    ext = path.suffix.lower()
    if ext in EXT_KINDS:
        return EXT_KINDS[ext], f"ext:{ext}"
    raise ValueError(f"unsupported file type: {path.name}")
