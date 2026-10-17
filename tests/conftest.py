"""Shared fixtures. All test files are generated here at test time; tests never touch personal files."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from vault import db
from vault.config import Config


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """Config with a throwaway data dir. The model cache stays shared so slow tests don't re-download."""
    return Config(data_dir=tmp_path / "data")


@pytest.fixture
def conn(cfg: Config) -> Iterator[sqlite3.Connection]:
    c = db.connect(cfg.db_path)
    yield c
    c.close()
