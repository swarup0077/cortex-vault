"""Lazy model singletons (SPEC §7.8). Nothing loads at import time.

Tests swap in fakes with `set_text_embedder()` / `set_clip()` / `set_ocr_engine()` so only the slow tests load
real weights.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

from vault.config import Config
from vault.ingest.records import OcrLine

log = logging.getLogger(__name__)


class TextEmbedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        """(n, dim) float32, L2-normalised."""
        ...

    def embed_query(self, text: str) -> np.ndarray:
        """(dim,) float32, L2-normalised."""
        ...


def l2_normalise(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.where(norms == 0, 1, norms)


class FastembedTextEmbedder:
    def __init__(self, cfg: Config) -> None:
        from fastembed import TextEmbedding

        self.name = cfg.text_model
        self.dim = cfg.text_dim
        self._batch = cfg.text_batch
        cfg.models_dir.mkdir(parents=True, exist_ok=True)
        log.info("loading text model %s", self.name)
        self._model = TextEmbedding(self.name, cache_dir=str(cfg.models_dir))

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), np.float32)
        return l2_normalise(np.stack(list(self._model.embed(list(texts), batch_size=self._batch))))

    def embed_query(self, text: str) -> np.ndarray:
        return l2_normalise(next(iter(self._model.query_embed([text]))))


_lock = threading.Lock()
_text: TextEmbedder | None = None


def text_embedder(cfg: Config | None = None) -> TextEmbedder:
    global _text
    with _lock:
        if _text is None:
            _text = FastembedTextEmbedder(cfg or Config())
        return _text


def set_text_embedder(embedder: TextEmbedder | None) -> None:
    """Install a specific embedder (tests), or None to fall back to lazy loading."""
    global _text
    with _lock:
        _text = embedder


# ---------------------------------------------------------------- CLIP (SPEC §4): images and queries in one space


class ClipVision(Protocol):
    name: str
    dim: int

    def embed_images(self, images: Sequence[Image.Image]) -> np.ndarray:
        """(n, dim) float32, L2-normalised. Images are RGB and upright."""
        ...


class ClipText(Protocol):
    name: str
    dim: int

    def embed_text(self, text: str) -> np.ndarray:
        """(dim,) float32, L2-normalised."""
        ...


def clip_model_name(cfg: Config) -> str:
    """What `meta.clip_model` records: both halves, since vectors from one only match queries from the other."""
    return f"{cfg.clip_vision_model}+{cfg.clip_text_model}"


class FastembedClipVision:
    def __init__(self, cfg: Config) -> None:
        from fastembed import ImageEmbedding

        self.name = clip_model_name(cfg)
        self.dim = cfg.clip_dim
        self._batch = cfg.clip_batch
        cfg.models_dir.mkdir(parents=True, exist_ok=True)
        log.info("loading CLIP vision model %s", cfg.clip_vision_model)
        self._model = ImageEmbedding(cfg.clip_vision_model, cache_dir=str(cfg.models_dir))

    def embed_images(self, images: Sequence[Image.Image]) -> np.ndarray:
        if not images:
            return np.zeros((0, self.dim), np.float32)
        return l2_normalise(np.stack(list(self._model.embed(list(images), batch_size=self._batch))))


class FastembedClipText:
    def __init__(self, cfg: Config) -> None:
        from fastembed import TextEmbedding

        self.name = clip_model_name(cfg)
        self.dim = cfg.clip_dim
        cfg.models_dir.mkdir(parents=True, exist_ok=True)
        log.info("loading CLIP text model %s", cfg.clip_text_model)
        self._model = TextEmbedding(cfg.clip_text_model, cache_dir=str(cfg.models_dir))

    def embed_text(self, text: str) -> np.ndarray:
        return l2_normalise(next(iter(self._model.embed([text]))))


_clip_vision: ClipVision | None = None
_clip_text: ClipText | None = None


def clip_vision_name(cfg: Config) -> str:
    """The CLIP model's name without loading it: an installed one's (a fake, in tests), else the configured."""
    return _clip_vision.name if _clip_vision is not None else clip_model_name(cfg)


def clip_vision(cfg: Config | None = None) -> ClipVision:
    global _clip_vision
    with _lock:
        if _clip_vision is None:
            _clip_vision = FastembedClipVision(cfg or Config())
        return _clip_vision


def clip_text(cfg: Config | None = None) -> ClipText:
    global _clip_text
    with _lock:
        if _clip_text is None:
            _clip_text = FastembedClipText(cfg or Config())
        return _clip_text


def set_clip(vision: ClipVision | None, text: ClipText | None) -> None:
    """Install specific CLIP halves (tests), or None to fall back to lazy loading."""
    global _clip_vision, _clip_text
    with _lock:
        _clip_vision, _clip_text = vision, text


# ---------------------------------------------------------------- OCR


class OcrEngine(Protocol):
    name: str

    def read(self, image: np.ndarray, *, use_cls: bool | None = None) -> list[OcrLine]:
        """Lines in `image` (H×W×3 RGB uint8), boxes in its pixels. Not yet filtered or ordered.

        `use_cls` overrides the configured text-angle classifier for this call (screenshots skip it).
        """
        ...


class RapidOcrEngine:
    """RapidOCR on ONNX Runtime, using the model files bundled in the wheel (never downloaded)."""

    def __init__(self, cfg: Config) -> None:
        import rapidocr
        from rapidocr import RapidOCR

        bundled = Path(rapidocr.__file__).parent / "models"
        # A bare name is a model file bundled in the wheel; a path is a model from elsewhere (bench/fetch_ocr_models.py)
        paths = {key: Path(name) if Path(name).is_absolute() else bundled / name for key, name in
                 (("Det", cfg.ocr_det_model), ("Rec", cfg.ocr_rec_model), ("Cls", cfg.ocr_cls_model))}
        missing = [str(p) for p in paths.values() if not p.is_file()]
        if missing:
            raise FileNotFoundError(f"bundled OCR models not found (rapidocr version changed?): {missing}")
        self.name = f"rapidocr:{cfg.ocr_det_model}+{cfg.ocr_rec_model}"
        self._use_cls = cfg.ocr_use_cls
        log.info("loading OCR models %s", self.name)
        self._ocr = RapidOCR(params={
            "Global.log_level": "error",  # an image with no text is normal; the index report counts them
            # Every recognised line comes back: merge_strips drops those below ocr_min_conf, and counting the
            # ones it drops tells a page of unreadable ink (handwriting) from a blank one (SPEC §6.3, item k)
            "Global.text_score": 0.0,
            "Global.use_cls": cfg.ocr_use_cls,
            "Global.max_side_len": cfg.ocr_max_side,  # tiles are already sized; don't shrink them again
            "Det.model_path": str(paths["Det"]),
            "Det.limit_side_len": cfg.ocr_det_limit_side,
            "Det.limit_type": cfg.ocr_det_limit_type,
            "Rec.model_path": str(paths["Rec"]),
            "Rec.rec_batch_num": cfg.ocr_rec_batch,
            # The bundled models carry their character list in the ONNX metadata; other exports need a keys file
            **({"Rec.rec_keys_path": str(cfg.ocr_rec_keys)} if cfg.ocr_rec_keys else {}),
            "Cls.model_path": str(paths["Cls"]),
            "EngineConfig.onnxruntime.intra_op_num_threads": cfg.ocr_threads or -1,
        })
        self.last_elapse: list[float | None] = []  # det / cls / rec seconds of the last call (profiling)

    def read(self, image: np.ndarray, *, use_cls: bool | None = None) -> list[OcrLine]:
        # Always pass a bool: RapidOCR stores a per-call use_cls on the instance and reads None as "keep the
        # last one", so one screenshot (use_cls=False) would switch the classifier off for every later photo.
        use_cls = self._use_cls if use_cls is None else use_cls
        out = self._ocr(np.ascontiguousarray(image[:, :, ::-1]), use_cls=use_cls)  # RapidOCR takes BGR arrays
        self.last_elapse = list(getattr(out, "elapse_list", []))
        if getattr(out, "boxes", None) is None or getattr(out, "txts", None) is None:
            return []
        lines = []
        for box, text, score in zip(out.boxes, out.txts, out.scores):
            xs, ys = box[:, 0], box[:, 1]
            lines.append(OcrLine(text, float(score), float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())))
        return lines


_ocr: OcrEngine | None = None


def ocr_engine(cfg: Config | None = None) -> OcrEngine:
    global _ocr
    with _lock:
        if _ocr is None:
            _ocr = RapidOcrEngine(cfg or Config())
        return _ocr


def set_ocr_engine(engine: OcrEngine | None) -> None:
    """Install a specific OCR engine (tests, tuning), or None to fall back to lazy loading."""
    global _ocr
    with _lock:
        _ocr = engine
