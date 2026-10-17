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

    # Chunking (SPEC §6.4)
    chunk_target: int = 800
    chunk_overlap: int = 150
    chunk_max: int = 1200

    # Search (SPEC §7)
    top_k: int = 100
    rrf_k: int = 60
    weights: dict[str, float] = field(default_factory=lambda: {"bm25": 1.0, "dense": 1.0})
    snippet_chars: int = 200
    max_results: int = 20

    @property
    def db_path(self) -> Path:
        return self.data_dir / "vault.db"


def load_config() -> Config:
    return Config()
