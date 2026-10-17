from pathlib import Path

import pytest

from vault.ingest.classify import classify
from vault.ingest.discover import sha256_file, walk, walk_roots


def _touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_walk_filters(tmp_path: Path) -> None:
    root = tmp_path / "My Docs"
    keep = [
        _touch(root / "notes one.txt"),
        _touch(root / "sub folder" / "deck.PPTX"),
        _touch(root / "paper.pdf"),
        _touch(root / "readme.md"),
        _touch(root / "letter.docx"),
    ]
    _touch(root / "~$letter.docx")  # Office lock file
    _touch(root / ".hidden" / "secret.txt")
    _touch(root / "__pycache__" / "x.txt")
    _touch(root / ".dotfile.txt")
    _touch(root / "photo.jpg")  # images join in M2
    _touch(root / "sheet.xlsx")

    found = {f.path for f in walk(root)}
    assert found == {p.resolve() for p in keep}
    assert all(f.root == root.resolve() for f in walk(root))


def test_walk_roots_dedupes_nested(tmp_path: Path) -> None:
    f = _touch(tmp_path / "a" / "b" / "x.txt")
    found = walk_roots([tmp_path / "a", tmp_path / "a" / "b"])
    assert [x.path for x in found] == [f.resolve()]
    assert found[0].root == (tmp_path / "a").resolve()


def test_sha256(tmp_path: Path) -> None:
    p = _touch(tmp_path / "x.txt", b"abc")
    assert sha256_file(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


@pytest.mark.parametrize(
    "name,kind",
    [("a.pdf", "pdf"), ("a.PDF", "pdf"), ("a.docx", "doc"), ("a.txt", "doc"), ("a.md", "doc"), ("a.pptx", "slides")],
)
def test_classify_by_extension(name: str, kind: str) -> None:
    assert classify(Path(name)) == (kind, f"ext:{Path(name).suffix.lower()}")


def test_classify_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        classify(Path("a.xlsx"))
