"""Lazy model singletons (SPEC §7.8). Nothing loads at import time.

Tests swap in fakes with `set_text_embedder()` / `set_ocr_engine()` so only the slow tests load real weights.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np

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


# ---------------------------------------------------------------- OCR


class OcrEngine(Protocol):
    name: str

    def read(self, image: np.ndarray) -> list[OcrLine]:
        """Lines in `image` (H×W×3 RGB uint8), boxes in its pixels. Not yet filtered or ordered."""
        ...


class RapidOcrEngine:
    """RapidOCR on ONNX Runtime, using the model files bundled in the wheel (never downloaded)."""

    def __init__(self, cfg: Config) -> None:
        import rapidocr
        from rapidocr import RapidOCR

        bundled = Path(rapidocr.__file__).parent / "models"
        paths = {
            "Det": bundled / cfg.ocr_det_model,
            "Rec": bundled / cfg.ocr_rec_model,
            "Cls": bundled / cfg.ocr_cls_model,
        }
        missing = [str(p) for p in paths.values() if not p.is_file()]
        if missing:
            raise FileNotFoundError(f"bundled OCR models not found (rapidocr version changed?): {missing}")
        self.name = f"rapidocr:{cfg.ocr_det_model}+{cfg.ocr_rec_model}"
        log.info("loading OCR models %s", self.name)
        self._ocr = RapidOCR(params={
            "Global.log_level": "warning",
            "Global.text_score": cfg.ocr_min_conf,
            "Global.use_cls": cfg.ocr_use_cls,
            "Global.max_side_len": cfg.ocr_max_side,  # tiles are already sized; don't shrink them again
            "Det.model_path": str(paths["Det"]),
            "Det.limit_side_len": cfg.ocr_det_limit_side,
            "Det.limit_type": cfg.ocr_det_limit_type,
            "Rec.model_path": str(paths["Rec"]),
            "Rec.rec_batch_num": cfg.ocr_rec_batch,
            "Cls.model_path": str(paths["Cls"]),
        })
        self.last_elapse: list[float | None] = []  # det / cls / rec seconds of the last call (profiling)

    def read(self, image: np.ndarray) -> list[OcrLine]:
        out = self._ocr(np.ascontiguousarray(image[:, :, ::-1]))  # RapidOCR takes arrays as BGR
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
