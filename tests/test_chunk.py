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
    assert path_chunk_text(paths) == "Goa trip 2025 / IMG_2041"  # no extension: "pdf" mustn't match every PDF
    assert path_chunk_text([Path("/a/b.txt"), Path("/c/d.txt")]) == "a / b\nc / d"
    assert path_chunk_text([Path("/a/b.txt"), Path("/a/b.md")]) == "a / b"


# ---------------------------------------------------------------- OCR chunks (block-aware, SPEC §6.4)

from vault.ingest.chunk import chunk_ocr  # noqa: E402
from vault.ingest.records import OcrLine  # noqa: E402


def ocr_block(block: int, n_lines: int, chars: int, start: int = 0) -> list[OcrLine]:
    """`n_lines` lines of ~`chars` characters each, all in `block`."""
    return [
        OcrLine(f"b{block}l{i} " + "x" * (chars - 6), 0.9, 0, 30 * (start + i), 500, 30 * (start + i) + 20,
                block=block, line=start + i)
        for i in range(n_lines)
    ]


def test_screenshot_sized_text_is_one_chunk() -> None:
    lines = ocr_block(0, 5, 60) + ocr_block(1, 4, 60, start=5)
    [c] = chunk_ocr(lines, None, CFG)
    assert c.source == "ocr" and c.lines == tuple(lines)
    assert c.text == " ".join(l.text for l in lines)


def test_blocks_are_never_split_below_chunk_max() -> None:
    a, b, c = ocr_block(0, 10, 60), ocr_block(1, 15, 60, start=10), ocr_block(2, 3, 60, start=25)
    chunks = chunk_ocr(a + b + c, 4, CFG)  # b alone is ~915 chars: over the target, under the max
    assert [[l.block for l in ch.lines] for ch in chunks] == [[0] * 10, [1] * 15, [2] * 3]
    assert {ch.page for ch in chunks} == {4}


def test_huge_block_is_split_on_lines_with_overlap() -> None:
    lines = ocr_block(0, 40, 60)  # ~2,440 chars
    chunks = chunk_ocr(lines, None, CFG)
    assert len(chunks) >= 3
    assert all(len(ch.text) <= CFG.chunk_target for ch in chunks)
    assert chunks[0].lines[-1] in chunks[1].lines  # overlap repeats trailing lines
    covered = {l.line for ch in chunks for l in ch.lines}
    assert covered == set(range(40))


def test_extracted_ocr_segments_follow_their_page() -> None:
    ex = Extracted(segments=[
        Segment("Slide title here", 1, "pdf_text"),
        Segment("ocr words", 1, "ocr", tuple(ocr_block(0, 1, 20))),
        Segment("Next page text", 2, "pdf_text"),
    ])
    assert [(c.page, c.source) for c in chunk_extracted(ex, CFG)] == [(1, "pdf_text"), (1, "ocr"), (2, "pdf_text")]
