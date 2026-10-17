from pathlib import Path

import pytest

from vault.config import Config
from vault.ingest.extract_office import extract_docx, extract_pptx, extract_text, zip_guard
from vault.ingest.extract_pdf import extract_pdf, parse_pdf_date
from vault.ingest.records import SkipFile


def test_pdf_text_per_page(tmp_path: Path, make_pdf) -> None:
    p = make_pdf(tmp_path / "a b.pdf", ["Intro page", "", "Singular value decomposition is defined here"])
    ex = extract_pdf(p)
    assert ex.pages == 3
    assert [s.page for s in ex.segments] == [1, 3]  # blank page 2 yields nothing
    assert "Singular value decomposition" in ex.segments[1].text
    assert all(s.source == "pdf_text" for s in ex.segments)
    assert ex.doc_date == "2025-03-12T10:15:30"


def test_pdf_encrypted_is_skipped(tmp_path: Path, make_pdf) -> None:
    p = make_pdf(tmp_path / "locked.pdf", ["secret"], password="pw")
    with pytest.raises(SkipFile, match="encrypted"):
        extract_pdf(p)


def test_pdf_corrupt_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"not a pdf at all")
    with pytest.raises(Exception):
        extract_pdf(p)


@pytest.mark.parametrize(
    "raw,iso",
    [
        ("D:20250312101530+05'30'", "2025-03-12T10:15:30"),
        ("D:2024", "2024-01-01T00:00:00"),
        ("garbage", None),
        (None, None),
        ("D:20251399", None),
    ],
)
def test_parse_pdf_date(raw, iso) -> None:
    assert parse_pdf_date(raw) == iso


def test_docx_paragraphs_then_tables(tmp_path: Path, make_docx) -> None:
    p = make_docx(tmp_path / "n.docx", ["First para", "", "Second para"], table=[["k", "v"], ["a", "b"]])
    ex = extract_docx(p, Config(data_dir=tmp_path))
    assert [s.text for s in ex.segments] == ["First para", "Second para", "k | v", "a | b"]
    assert all(s.page is None and s.source == "doc_text" for s in ex.segments)


def test_pptx_per_slide(tmp_path: Path, make_pptx) -> None:
    p = make_pptx(
        tmp_path / "deck.pptx",
        [("Welcome", "agenda items", ""), ("Gradient descent", "step downhill", "say: learning rate")],
    )
    ex = extract_pptx(p, Config(data_dir=tmp_path))
    assert ex.pages == 2
    assert [s.page for s in ex.segments] == [1, 2]
    s1, s2 = (s.text for s in ex.segments)
    assert s1.startswith("Welcome") and "cell01" in s1 and s1.count("Welcome") == 1
    assert s2.splitlines() == ["Gradient descent", "step downhill", "say: learning rate"]


def test_text_paragraphs(tmp_path: Path) -> None:
    p = tmp_path / "n.md"
    p.write_bytes("# Title\n\nbody one\nstill one\n\n\n  \nbody two \xff".encode("latin-1"))
    ex = extract_text(p)
    assert [s.text for s in ex.segments][:2] == ["# Title", "body one\nstill one"]
    assert ex.segments[2].text.startswith("body two") and "�" in ex.segments[2].text


def test_zip_guard(tmp_path: Path, make_zip_bomb) -> None:
    bomb = make_zip_bomb(tmp_path / "bomb.docx")
    with pytest.raises(SkipFile, match="ratio"):
        zip_guard(bomb, 200 * 2**20, 100)
    with pytest.raises(SkipFile, match="uncompressed"):
        zip_guard(bomb, 10 * 2**20, 1e9)
    with pytest.raises(SkipFile):
        extract_docx(bomb, Config(data_dir=tmp_path))


def test_real_docx_passes_zip_guard(tmp_path: Path, make_docx) -> None:
    zip_guard(make_docx(tmp_path / "ok.docx", ["hello"]), 200 * 2**20, 100)
