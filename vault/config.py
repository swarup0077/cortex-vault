"""Configuration: one frozen dataclass, with environment overrides (SPEC §13)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _default_data_dir() -> Path:
    return Path(os.environ.get("VAULT_DATA_DIR") or REPO_ROOT / "data")


def _default_models_dir() -> Path:
    env = os.environ.get("FASTEMBED_CACHE_PATH") or os.environ.get("VAULT_MODELS_DIR")
    return Path(env) if env else REPO_ROOT / "data" / "models"


def _default_ocr_threads() -> int:
    """8 measured best on the dev laptop (SPEC §19); never more than the machine has (a 4-vCPU VM uses 4)."""
    return min(8, os.cpu_count() or 1)


@dataclass(frozen=True)
class Config:
    data_dir: Path = field(default_factory=_default_data_dir)
    models_dir: Path = field(default_factory=_default_models_dir)

    # Models (SPEC §4)
    text_model: str = "BAAI/bge-small-en-v1.5"
    text_dim: int = 384
    text_batch: int = 32

    # Zip guard for .docx/.pptx (SPEC §6.3): targets bombs, not big decks
    zip_max_ratio: float = 100.0  # per member over 1 MB unpacked
    zip_max_xml_bytes: int = 50 * 1024 * 1024  # XML parts only; media isn't counted
    zip_max_total: int | None = None  # absolute cap; set only for demo uploads (M4)

    # OCR (SPEC §6.3). Model files ship inside the rapidocr wheel; nothing is downloaded.
    ocr_det_model: str = "PP-OCRv6_det_small.onnx"
    ocr_rec_model: str = "PP-OCRv6_rec_small.onnx"
    ocr_cls_model: str = "ch_ppocr_mobile_v2.0_cls_mobile.onnx"
    # Speed settings measured on the dev laptop (SPEC §19, OCR benchmark): 1080p screenshot 4.5 s → 2.4 s.
    ocr_det_limit_side: int = 960  # detector input size (RapidOCR's Det.limit_side_len)
    ocr_det_limit_type: str = "max"  # 'max': long side at most the limit; 'min': short side at least it
    ocr_use_cls: bool = True  # text-angle classifier (flips upside-down lines): photos and PDF pages
    ocr_cls_screenshots: bool = False  # on-screen text is never upside down; skipping it also misreads less
    ocr_rec_batch: int = 6
    # ONNX Runtime intra-op threads per OCR session; 0 = ONNX Runtime's own default
    ocr_threads: int = field(default_factory=_default_ocr_threads)
    ocr_min_conf: float = 0.5
    # An image whose letters are at least this share Latin is "mostly Latin": its lines that are only one or
    # two CJK characters are misread icons and are dropped (SPEC §19)
    ocr_latin_share: float = 0.8
    ocr_max_side: int = 2048  # wider images are scaled to this width; taller ones are cut into strips
    ocr_strip: int = 1600
    ocr_strip_overlap: int = 200
    ocr_max_strips: int = 15
    ocr_block_gap: float = 1.5  # × median line height: a bigger gap starts a new block / cuts a region
    ocr_gutter: float = 0.5  # × median line height: the narrowest gap that counts as a column gutter

    # PDF OCR (SPEC §6.3)
    pdf_ocr_dpi: int = 150
    pdf_ocr_max_pages: int = 30
    pdf_scanned_chars: int = 20  # fewer native chars than this: OCR the whole page
    pdf_region_min_frac: float = 0.15  # images covering at least this share of the page are OCR'd
    pdf_region_text_chars: int = 20  # ...unless this much native text already lies inside them

    # Chunking (SPEC §6.4)
    chunk_target: int = 800
    chunk_overlap: int = 150
    chunk_max: int = 1200

    # Search (SPEC §7)
    top_k: int = 100
    rrf_k: int = 60
    # RRF weight per ranked list; 0 leaves a list out (the ablations do). "all_terms" is BM25 restricted to chunks
    # holding every content term (SPEC §7.2, §19)
    weights: dict[str, float] = field(default_factory=lambda: {"bm25": 1.0, "all_terms": 1.0, "dense": 1.0})
    snippet_chars: int = 200
    max_results: int = 20

    @property
    def db_path(self) -> Path:
        return self.data_dir / "vault.db"


def load_config() -> Config:
    return Config()
