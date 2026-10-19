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
    clip_vision_model: str = "Qdrant/clip-ViT-B-32-vision"
    clip_text_model: str = "Qdrant/clip-ViT-B-32-text"
    clip_dim: int = 512
    clip_batch: int = 16
    clip_side: int = 448  # images are shrunk to this short side before CLIP (it centre-crops 224 px anyway)

    # Zip guard for .docx/.pptx (SPEC §6.3): targets bombs, not big decks
    zip_max_ratio: float = 100.0  # per member over 1 MB unpacked
    zip_max_xml_bytes: int = 50 * 1024 * 1024  # XML parts only; media isn't counted
    zip_max_total: int | None = None  # absolute cap; set only for demo uploads (M4)

    # OCR (SPEC §6.3). Model files ship inside the rapidocr wheel; nothing is downloaded.
    ocr_det_model: str = "PP-OCRv6_det_small.onnx"
    ocr_rec_model: str = "PP-OCRv6_rec_small.onnx"
    ocr_cls_model: str = "ch_ppocr_mobile_v2.0_cls_mobile.onnx"
    ocr_rec_keys: str | None = None  # character list for a recogniser whose ONNX file doesn't carry one
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
    # Early stop (open item k): after this many OCR'd pages with ink, stop OCR'ing a PDF whose lines the recogniser
    # read with a mean confidence below pdf_ocr_stop_conf (handwriting, mostly). Provisional: calibrated by
    # bench/calibrate.py ocr-stop on generated pages (SPEC §19): printed pages 0.87-0.99 (a dot-leader contents page
    # lowest), handwriting-like scribbles 0.50-0.58.
    pdf_ocr_probe_pages: int = 3
    pdf_ocr_stop_conf: float = 0.72
    pdf_scanned_chars: int = 20  # fewer native chars than this: OCR the whole page
    pdf_region_min_frac: float = 0.15  # images covering at least this share of the page are OCR'd
    pdf_region_text_chars: int = 20  # ...unless this much native text already lies inside them

    # Chunking (SPEC §6.4)
    chunk_target: int = 800
    chunk_overlap: int = 150
    chunk_max: int = 1200

    # Search (SPEC §7)
    top_k: int = 100
    # Dense hits less similar than this are dropped, at any collection size: dense always returns its top k, so
    # without a floor the only text file in a small collection was dense #1 for any query (open item s).
    # Provisional, on bge-small-en-v1.5's scale (bench/calibrate.py dense): 35 generated matching pairs, 24 of them
    # hard paraphrases, scored 0.539-0.832; the floor sits 2 std of the hard-pair scores (0.113) below the lowest,
    # rounded down. A floor too high costs paraphrase recall, so it errs low: it cuts 55% of unrelated pairs.
    dense_min_cosine: float = 0.42
    rrf_k: int = 60
    # RRF weight per ranked list; 0 leaves a list out (the ablations do). "all_terms" is BM25 restricted to chunks
    # holding every content term (SPEC §7.2, §19)
    weights: dict[str, float] = field(
        default_factory=lambda: {"bm25": 1.0, "all_terms": 1.0, "dense": 1.0, "clip": 1.0})
    # CLIP gate (SPEC §7.3); off only for the eval's "CLIP ungated" ablation (SPEC §9), which keeps CLIP's top k
    clip_gated: bool = True
    clip_gate_z: float = 2.0  # keep images scoring at least this many std devs above the candidates' mean
    clip_gate_max: int = 50
    clip_gate_min_candidates: int = 20  # fewer candidates than this: z is meaningless, keep the top few instead
    clip_gate_fallback_top: int = 5
    # ...and of those, only hits at least this similar. Provisional, from generated scenes (SPEC §19): matching
    # pairs scored 0.265-0.326, non-matching ones up to 0.258 bar one at 0.275. Tune with the eval in M6.
    clip_min_cosine: float = 0.26
    clip_code_weight: float = 0.5  # CLIP's RRF weight is multiplied by this for quoted or code-like queries
    # Visual-query rule (SPEC §7.4): when no file holds every content word (the all-terms list is empty), the query
    # is neither quoted nor code-like, and CLIP's top hit is a strong outlier, BM25's weight is multiplied by
    # visual_bm25_factor and each *strong* CLIP hit's by visual_clip_factor; weaker gated hits keep the normal
    # weight. CLIP's #1 then scores 2/61, above the 0.5/61 + 1/61 of a file that only BM25 #1 and dense #1 found.
    # Strong: z >= clip_strong_z, or with fewer than clip_gate_min_candidates images, cosine >= clip_strong_cosine.
    # All provisional (bench/calibrate.py rule: 12 drawn scenes, 35 paraphrase queries, real CLIP and bge). With
    # the rule off, 1 of 12 scenes was #1 (a file sharing one word with the query beat it). At z 3.25: 10/12 among
    # 36 images and 11/12 among 200, for no paraphrase #1 lost at 36 and one at 200 (every level 3.0-4.0 lost
    # that one). Under 20 images, cosine 0.28: 12/12, and the rule fired on no paraphrase.
    clip_strong_z: float = 3.25
    clip_strong_cosine: float = 0.28
    visual_bm25_factor: float = 0.5
    visual_clip_factor: float = 2.0
    # Screenshot vs photo for images no name, EXIF or PNG rule decides (SPEC §6.2): screenshot when the image's CLIP
    # cosine to the screenshot prompt leads the photo prompt's by at least this. Provisional (bench/calibrate.py
    # kind): on 56 generated screen captures and 42 photo-like pictures at forwarded sizes, 0.04 got 84% and 76%
    # right, the best balance. Generated "photos" aren't photographs, so M6 checks it on the corpus's real ones.
    clip_screenshot_margin: float = 0.04
    snippet_chars: int = 200
    # Result list (SPEC §8): the UI shows `page_results` cards, then "Show more" up to `max_results` (the CLI's
    # own default is -n 10). Below the fused list come the weaker matches (SPEC §7.4 step 4), up to the same cap;
    # the eval can switch them off. `visual_row` caps the Visual matches row (open item t).
    max_results: int = 50
    page_results: int = 20
    weaker_matches: bool = True
    visual_row: int = 10

    # Demo uploads (SPEC §8): enforced in vault/session.py, not only by Gradio. M6 (§19, 19 Oct): sized for a
    # small audience (one or two visitors at the viva): fewer sessions, each allowed more; the 200-file cap is timed
    # on the VM with `bench/bench_upload.py --files 200`.
    upload_max_files: int = 200  # per session, over all its uploads
    upload_max_bytes: int = 50 * 1024 * 1024  # per file
    upload_max_total: int = 500 * 1024 * 1024  # per session
    upload_zip_max_total: int = 500 * 1024 * 1024  # zip_max_total for uploaded .docx/.pptx (unpacked)
    session_ttl_s: int = 60 * 60  # wiped this long after the session's last search or upload
    max_sessions: int = 5  # sessions at once; more uploaders are turned away (disk: 5 × 500 MB plus indexes)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "vault.db"


def load_config() -> Config:
    return Config()
