from pathlib import Path

import pytest

from vault.__main__ import _snippet_markup, main


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "data"
    monkeypatch.setenv("VAULT_DATA_DIR", str(d))
    monkeypatch.setenv("COLUMNS", "200")
    return d


def test_index_search_stats(data_dir, fake_embedder, tmp_path, make_search_corpus, capsys) -> None:
    root = make_search_corpus(tmp_path / "my files")
    assert main(["index", str(root)]) == 0
    out = capsys.readouterr().out
    assert "added" in out and "6" in out
    assert (data_dir / "vault.db").exists()

    assert main(["search", "singular value decomposition", "--debug"]) == 0
    out = capsys.readouterr().out
    assert "linear algebra notes.pdf" in out and "page 2" in out and "timings ms" in out

    assert main(["search", "gradient descent"]) == 0
    assert "slide 3" in capsys.readouterr().out

    assert main(["stats"]) == 0
    out = capsys.readouterr().out
    assert "pdf" in out and "slides" in out and fake_embedder.name in out and str(root.resolve()) in out
    assert "OCR lines 0" in out

    assert main(["reindex"]) == 0
    assert "added" in capsys.readouterr().out


def test_model_mismatch_exit_code(data_dir, fake_embedder, tmp_path, make_search_corpus, capsys) -> None:
    root = make_search_corpus(tmp_path / "c")
    main(["index", str(root)])
    fake_embedder.name = "something-else"
    assert main(["search", "matrix"]) == 2
    assert "reindex" in capsys.readouterr().out


def test_snippet_markup_escapes_rich() -> None:
    assert _snippet_markup("a [b] <mark>c&amp;d</mark>") == "a \\[b] [bold yellow]c&d[/]"
