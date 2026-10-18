"""Download the official PP-OCRv6 *tiny* ONNX models for the OCR comparison (open item j), from the HF Hub only.

PaddlePaddle publishes them under Apache-2.0 (huggingface.co/PaddlePaddle/PP-OCRv6_tiny_rec_onnx and
.../PP-OCRv6_tiny_det_onnx). The files are pinned to the commits checked on 18 Oct 2026 and saved under
bench/generated/models/ (gitignored); the recogniser's character list is written from its inference.yml, which
RapidOCR reads as `Rec.rec_keys_path`. Nothing here is used by the app unless config points at it.

    python bench/fetch_ocr_models.py
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "bench" / "generated" / "models"

MODELS = {  # repo: (commit, local name)
    "PaddlePaddle/PP-OCRv6_tiny_rec_onnx": ("2612ab37152ae0a677521bae4e1e3d4fb4cf7c30", "PP-OCRv6_tiny_rec"),
    "PaddlePaddle/PP-OCRv6_tiny_det_onnx": ("2ba1506c0380b8f0b03dd142459aac66d4421f6c", "PP-OCRv6_tiny_det"),
}


def fetch(repo: str, commit: str, name: str, dest: Path) -> Path:
    url = f"https://huggingface.co/{repo}/resolve/{commit}/{name}"
    if not dest.exists():
        print(f"downloading {url}")
        with urllib.request.urlopen(url, timeout=120) as r:
            dest.write_bytes(r.read())
    return dest


def character_dict(yml: str) -> list[str]:
    """The `character_dict` list under PostProcess in a PaddleOCR inference.yml, without a YAML library."""
    lines = yml.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("character_dict:"))
    indent = len(lines[start]) - len(lines[start].lstrip())
    chars = []
    for ln in lines[start + 1:]:
        stripped = ln.strip()
        if not stripped.startswith("- ") and stripped != "-":
            if len(ln) - len(ln.lstrip()) <= indent and stripped:
                break
            continue
        value = ln.split("- ", 1)[1] if "- " in ln else ""
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1].replace("''", "'") if value[0] == "'" else bytes(value[1:-1], "utf-8").decode(
                "unicode_escape")
        chars.append(value)
    return chars


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for repo, (commit, local) in MODELS.items():
        onnx = fetch(repo, commit, "inference.onnx", OUT / f"{local}.onnx")
        yml = fetch(repo, commit, "inference.yml", OUT / f"{local}.yml")
        print(f"{local}.onnx  {onnx.stat().st_size / 2**20:.1f} MB  sha256 {hashlib.sha256(onnx.read_bytes()).hexdigest()[:16]}")
        if "rec" in local:
            chars = character_dict(yml.read_text(encoding="utf-8"))
            (OUT / f"{local}_keys.txt").write_text("\n".join(chars) + "\n", encoding="utf-8")
            print(f"{local}_keys.txt  {len(chars)} characters")


if __name__ == "__main__":
    sys.exit(main())
