"""Lazy model singletons (SPEC §7.8). Nothing loads at import time.

Tests swap in a fake with `set_text_embedder()` so only the slow tests load real weights.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Protocol

import numpy as np

from vault.config import Config

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
