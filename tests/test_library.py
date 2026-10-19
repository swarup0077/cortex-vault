"""Library: one index as the app sees it (cached vectors, files served by id only, detail, stats)."""

import shutil
from pathlib import Path

from PIL import Image

from conftest import build_pdf, camera_exif, save_image
from vault import db
from vault.config import Config
from vault.library import Library


def make_library(tmp_path: Path, make_search_corpus) -> tuple[Library, Path]:
    root = make_search_corpus(tmp_path / "my files")
    save_image(Image.new("RGB", (640, 480), (220, 30, 30)), root / "photos" / "IMG_red.jpg", exif=camera_exif())
    lib = Library(Config(data_dir=tmp_path / "data"))
    lib.index([root])
    return lib, root


def test_search_uses_cached_vectors_rebuilt_after_indexing(tmp_path, fake_embedder, make_search_corpus,
                                                           monkeypatch) -> None:
    lib, root = make_library(tmp_path, make_search_corpus)
    assert lib.search("singular value decomposition").results[0].page == 2
    loads = []
    from vault.search import retrievers
    real = retrievers.load_text_index
    monkeypatch.setattr(retrievers, "load_text_index", lambda *a: loads.append(1) or real(*a))
    lib.search("sourdough")
    assert loads == []  # cached
    (root / "home" / "boarding.txt").write_text("Boarding pass for gate 7.", encoding="utf-8")
    lib.index([root])
    assert loads == [1]  # rebuilt once after indexing
    assert lib.search("boarding pass").results[0].filename == "boarding.txt"


def test_files_are_served_by_id_only(tmp_path, fake_embedder, make_search_corpus) -> None:
    lib, _ = make_library(tmp_path, make_search_corpus)
    hit = lib.search("red").results[0]
    assert lib.thumb(hit.file_id).suffix == ".webp"
    assert lib.thumb(999_999) is None
    assert lib.file_path(hit.file_id).name == "IMG_red.jpg"
    assert lib.preview(hit.file_id).size == (640, 480)


def test_preview_renders_the_matched_pdf_page(tmp_path, fake_embedder, make_search_corpus) -> None:
    lib, _ = make_library(tmp_path, make_search_corpus)
    hit = lib.search("singular value decomposition").results[0]
    page2 = lib.preview(hit.file_id, page=2)
    assert page2.width >= 1000 and page2.height > page2.width  # a portrait page
    assert lib.preview(hit.file_id, page=99) is not None  # out of range: page 1
    doc = lib.search("electricity invoice").results[0]
    assert lib.preview(doc.file_id).size == lib.thumb_image(doc.file_id).size  # no renderer: the card


def test_detail_has_the_full_chunk_highlighted_and_every_path(tmp_path, fake_embedder, make_search_corpus) -> None:
    lib, root = make_library(tmp_path, make_search_corpus)
    shutil.copy(root / "home" / "recipe.txt", root / "recipe copy.txt")
    lib.index([root])
    hit = lib.search("sourdough starter").results[0]
    d = lib.detail(hit.file_id, hit.chunk_id, "sourdough starter")
    assert "<mark>Sourdough</mark>" in d.chunk_html and "<mark>starter</mark>" in d.chunk_html
    assert len(d.paths) == 2 and d.kind == "doc"


def test_repo_relative_paths_resolve_against_the_base_dir(tmp_path, fake_embedder) -> None:
    base = tmp_path / "repo"
    build_pdf(base / "corpus" / "papers" / "a.pdf", ["Denoising diffusion models."])
    lib = Library(Config(data_dir=tmp_path / "data"), base_dir=base)
    lib.index([base / "corpus"])
    with lib.connect() as conn:
        conn.execute("UPDATE paths SET path = 'corpus/papers/a.pdf', root = 'corpus'")
    hit = lib.search("diffusion").results[0]
    assert lib.file_path(hit.file_id) == base / "corpus" / "papers" / "a.pdf"


def test_read_only_library_serves_thumbs_from_its_index_dir(tmp_path, fake_embedder, make_search_corpus) -> None:
    lib, _ = make_library(tmp_path, make_search_corpus)
    ro = Library(Config(data_dir=tmp_path / "elsewhere"), db_path=lib.db_path, read_only=True,
                 thumb_dirs=[lib.cfg.data_dir])
    hit = ro.search("red").results[0]
    assert ro.thumb(hit.file_id) is not None


def test_stats_and_remove_root(tmp_path, fake_embedder, make_search_corpus) -> None:
    lib, root = make_library(tmp_path, make_search_corpus)
    (root / "broken.pdf").write_bytes(b"not a pdf")
    lib.index([root])
    st = lib.stats()
    assert st.counts["pdf"] == 2 and st.counts["photo"] == 1 and st.roots == [str(root.resolve())]
    assert [Path(e["path"]).name for e in st.errors] == ["broken.pdf"]
    assert st.last_run["seen"] == 8 and st.last_indexed
    assert lib.remove_root(str(root.resolve())) == 8
    assert lib.search("sourdough").results == [] and lib.stats().roots == []
    with lib.connect() as conn:
        assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0
        assert db.get_roots(conn) == []


def test_type_counts_split_plain_text_from_documents(tmp_path, fake_embedder, make_search_corpus) -> None:
    """The Archive's counts (SPEC §8): Text is a .txt / .md doc, Document the other docs."""
    lib, _ = make_library(tmp_path, make_search_corpus)
    assert lib.type_counts() == {"screenshot": 0, "pdf": 2, "text": 1, "photo": 1, "doc": 2, "slides": 1}


def test_recent_by_type_has_the_newest_of_each_type(tmp_path, fake_embedder, make_search_corpus) -> None:
    lib, _ = make_library(tmp_path, make_search_corpus)
    recent = lib.recent_by_type(1)
    names = {r.name for r in recent}
    assert len(recent) == 5 and {"recipe.txt", "IMG_red.jpg", "ml lecture.pptx"} <= names
    assert len(names & {"household.docx", "travel.docx"}) == 1 and recent[0].name == lib.recent(1)[0].name
    assert [r.indexed_at for r in recent] == sorted((r.indexed_at for r in recent), reverse=True)
