from pathlib import Path

from vault.config import Config
from vault.ingest.chunk import chunk_extracted, chunk_paragraphs, normalise, path_chunk_text, windows
from vault.ingest.records import Extracted, Segment

CFG = Config(data_dir=Path("unused"))


def words(n: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def test_normalise() -> None:
    assert normalise("  Hello\n\tWorld  \n") == "Hello World"


def test_windows_overlap_and_coverage() -> None:
    text = words(400)  # ~2,300 chars
    parts = windows(text, 800, 150)
    assert len(parts) > 2
    assert all(len(p) <= 800 for p in parts)
    for a, b in zip(parts, parts[1:]):
        tail = a[-100:].split(" ", 1)[1]  # whole words from the end of a
        assert tail in b  # overlap carried into the next window
    assert parts[0].startswith("w0 ") and parts[-1].endswith("w399")
    assert set(" ".join(parts).split()) == set(text.split())  # nothing lost, no split words


def test_short_page_is_one_chunk_long_page_is_windowed() -> None:
    ex = Extracted(
        segments=[Segment("short page", 1, "pdf_text"), Segment(words(300), 2, "pdf_text")], pages=2
    )
    chunks = chunk_extracted(ex, CFG)
    assert chunks[0].text == "short page" and chunks[0].page == 1
    assert len(chunks) > 2 and {c.page for c in chunks[1:]} == {2}


def test_page_up_to_max_stays_whole() -> None:
    text = "x" * 1100
    chunks = chunk_extracted(Extracted(segments=[Segment(text, 4, "slide_text")]), CFG)
    assert [(c.text, c.page) for c in chunks] == [(text, 4)]


def test_paragraph_chunks_cut_on_boundaries_with_overlap() -> None:
    paras = [f"Paragraph {i}. " + words(30, f"p{i}w") for i in range(20)]  # ~230 chars each
    chunks = chunk_paragraphs(paras, 800, 150, 1200)
    assert len(chunks) > 3
    for c in chunks:
        assert len(c) <= 1200
        for line in c.split("\n")[1:]:
            assert line.startswith("Paragraph ")  # whole paragraphs only, except a leading overlap tail
    for a, b in zip(chunks, chunks[1:]):
        assert a.split("\n")[-1][-50:] in b  # tail of a carried into b
    joined = "\n".join(chunks)
    assert all(p in joined for p in paras)


def test_short_paragraphs_overlap_whole() -> None:
    paras = ["a" * 300, "b" * 300, "c" * 100, "d" * 300]
    chunks = chunk_paragraphs(paras, 800, 150, 1200)
    assert chunks == ["a" * 300 + "\n" + "b" * 300 + "\n" + "c" * 100, "c" * 100 + "\n" + "d" * 300]


def test_oversized_paragraph_windowed_alone() -> None:
    big = words(400)
    chunks = chunk_paragraphs(["intro", big, "outro"], 800, 150, 1200)
    assert chunks[0] == "intro"
    assert chunks[-1] == "outro"
    assert all(len(c) <= 800 for c in chunks[1:-1])


def test_docx_chunks_have_no_page() -> None:
    ex = Extracted(segments=[Segment("one", None, "doc_text"), Segment("two", None, "doc_text")])
    assert [(c.text, c.page, c.source) for c in chunk_extracted(ex, CFG)] == [("one\ntwo", None, "doc_text")]


def test_path_chunk_text() -> None:
    paths = [Path("C:/Users/me/Goa trip 2025/IMG_2041.jpg"), Path("D:/backup/Goa trip 2025/IMG_2041.jpg")]
    assert path_chunk_text(paths) == "Goa trip 2025 / IMG_2041.jpg"
    assert path_chunk_text([Path("/a/b.txt"), Path("/c/d.txt")]) == "a / b.txt\nc / d.txt"
