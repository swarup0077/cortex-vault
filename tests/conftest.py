"""Shared fixtures. All test files are generated here at test time; tests never touch personal files."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import zipfile
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import docx
import numpy as np
import pptx
import pymupdf
import pytest
from pptx.util import Inches

from vault import db, models
from vault.config import Config

# ---------------------------------------------------------------- config / db


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """Config with a throwaway data dir. The model cache stays shared so slow tests don't re-download."""
    return Config(data_dir=tmp_path / "data")


@pytest.fixture
def conn(cfg: Config) -> Iterator[sqlite3.Connection]:
    c = db.connect(cfg.db_path)
    yield c
    c.close()


# ---------------------------------------------------------------- models


class FakeTextEmbedder:
    """Deterministic hashed bag-of-words. Records every text it embeds so tests can count re-embeds."""

    name = "fake-hash-embedder"
    dim = 64

    def __init__(self) -> None:
        self.embedded: list[str] = []

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, np.float32)
        for tok in re.findall(r"\w+", text.lower()):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_documents(self, texts):
        self.embedded += list(texts)
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)


@pytest.fixture
def fake_embedder() -> Iterator[FakeTextEmbedder]:
    e = FakeTextEmbedder()
    models.set_text_embedder(e)
    yield e
    models.set_text_embedder(None)


# ---------------------------------------------------------------- file builders


def build_pdf(
    path: Path, pages: Sequence[str], *, password: str | None = None, created: str = "D:20250312101530"
) -> Path:
    """A PDF with `pages[i]` as the native text of page i+1."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.set_metadata({"creationDate": created})
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=11)
    kwargs = {}
    if password:
        kwargs = dict(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw=password, user_pw=password)
    doc.save(path, **kwargs)
    doc.close()
    return path


def build_docx(path: Path, paragraphs: Sequence[str], table: Sequence[Sequence[str]] = ()) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    if table:
        t = d.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, val in enumerate(row):
                t.cell(r, c).text = val
    d.save(str(path))
    return path


def build_pptx(path: Path, slides: Sequence[tuple[str, str, str]]) -> Path:
    """`slides` are (title, body, speaker notes). Slide 1 is also given a 2×2 table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    prs = pptx.Presentation()
    for i, (title, body, notes) in enumerate(slides):
        slide = prs.slides.add_slide(prs.slide_layouts[1])  # title + content
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
        if i == 0:
            tbl = slide.shapes.add_table(2, 2, Inches(1), Inches(5), Inches(4), Inches(1)).table
            for r in range(2):
                for c in range(2):
                    tbl.cell(r, c).text = f"cell{r}{c}"
    prs.save(str(path))
    return path


def build_zip_bomb(path: Path, size: int = 50 * 2**20) -> Path:
    """A highly compressible archive with an Office extension."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"\0" * size)
    return path


@pytest.fixture
def make_pdf() -> Callable[..., Path]:
    return build_pdf


@pytest.fixture
def make_docx() -> Callable[..., Path]:
    return build_docx


@pytest.fixture
def make_pptx() -> Callable[..., Path]:
    return build_pptx


@pytest.fixture
def make_zip_bomb() -> Callable[..., Path]:
    return build_zip_bomb
