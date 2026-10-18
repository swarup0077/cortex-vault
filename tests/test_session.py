"""Demo uploads (SPEC §8): a per-session copy of the demo index, incremental uploads, limits enforced in code,
the 30-minute expiry, and a demo index that never changes."""

import hashlib
from pathlib import Path

import pytest

from conftest import build_pdf
from vault.config import Config
from vault.demo import build_demo
from vault.library import Library
from vault.session import SessionManager


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def demo(tmp_path, fake_embedder) -> Library:
    base = tmp_path / "repo"
    build_pdf(base / "corpus" / "papers" / "diffusion.pdf", ["Denoising diffusion models reverse a noising process."])
    (base / "corpus" / "notes").mkdir(parents=True)
    (base / "corpus" / "notes" / "recipe.txt").write_text("Sourdough with a ripe starter.", encoding="utf-8")
    build_demo(base / "corpus", base / "demo_index", Config(data_dir=tmp_path / "unused"), base=base)
    return Library(Config(data_dir=base / "demo_index"), read_only=True, base_dir=base)


@pytest.fixture
def inbox(tmp_path) -> Path:
    """Stands in for Gradio's upload folder."""
    d = tmp_path / "gradio"
    d.mkdir()
    return d


def make_files(inbox: Path, n: int, start: int = 0, size: int = 0) -> list[Path]:
    out = []
    for i in range(start, start + n):
        d = inbox / hashlib.md5(str(i).encode()).hexdigest()  # Gradio keeps each upload in its own folder
        d.mkdir(exist_ok=True)
        p = d / f"note {i:03}.txt"
        p.write_text(f"Upload number {i}: the quarterly report for project {i}." + "x" * size, encoding="utf-8")
        out.append(p)
    return out


def manager(demo: Library, tmp_path: Path, clock=None, **cfg) -> SessionManager:
    return SessionManager(demo, Config(data_dir=tmp_path / "unused", **cfg), tmp_path / "sessions",
                          clock=clock or Clock())


def demo_digest(demo: Library) -> str:
    return hashlib.sha256(demo.db_path.read_bytes()).hexdigest()


def test_uploads_are_incremental(demo, inbox, tmp_path) -> None:
    """Owner's addition (SPEC §8): 30 files, then 20 more; the second run indexes only the 20."""
    sm = manager(demo, tmp_path)
    before = demo_digest(demo)
    s, first = sm.add_files(None, make_files(inbox, 30), inbox)
    assert first.report.added == 30 and [f.status for f in first.files] == ["indexed"] * 30
    s2, second = sm.add_files(s.id, make_files(inbox, 20, start=30), inbox)
    assert s2.id == s.id
    assert second.report.added == 20 and second.report.unchanged == 30
    again, third = sm.add_files(s.id, make_files(inbox, 2, start=0), inbox)  # same bytes as two earlier uploads
    assert [f.status for f in third.files] == ["duplicate", "duplicate"] and third.report is None
    assert demo_digest(demo) == before  # the demo index never changes


def test_scope_my_uploads_samples_or_both(demo, inbox, tmp_path) -> None:
    sm = manager(demo, tmp_path)
    s, _ = sm.add_files(None, make_files(inbox, 3), inbox)
    lib = s.library
    mine = lib.search("quarterly report", roots=[s.root])
    assert len(mine.results) == 3
    assert lib.search("denoising diffusion", roots=[s.root]).results == []
    samples = lib.search("denoising diffusion", roots=["corpus"])
    assert samples.results[0].filename == "diffusion.pdf"
    assert {r.filename for r in lib.search("quarterly report").results} >= {"note 000.txt"}
    assert lib.display(str(s.uploads / "note 000.txt")) == "uploads/note 000.txt"
    assert lib.thumb(samples.results[0].file_id).is_file()  # the demo's thumbnails serve from a session too


def blob(inbox: Path, name: str, nbytes: int, seed: int) -> Path:
    """An upload of exactly `nbytes`, with unique content."""
    d = inbox / f"d{seed}"
    d.mkdir()
    head = f"file {seed} about budgets. "
    p = d / name
    p.write_bytes((head + "x" * (nbytes - len(head))).encode())
    return p


def test_limits_are_enforced_in_code(demo, inbox, tmp_path) -> None:
    sm = manager(demo, tmp_path, upload_max_files=4, upload_max_bytes=2000, upload_max_total=3000)
    exe = inbox / "x" / "setup.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    s, res = sm.add_files(None, [blob(inbox, "a.txt", 500, 1), blob(inbox, "b.txt", 500, 2),
                                 blob(inbox, "big.txt", 2500, 3), exe], inbox)
    assert [(f.name, f.status, f.reason) for f in res.files] == [
        ("a.txt", "indexed", ""), ("b.txt", "indexed", ""), ("big.txt", "rejected", "larger than 2 KB"),
        ("setup.exe", "rejected", "file type not supported (.exe)")]
    _, res2 = sm.add_files(s.id, [blob(inbox, "c.txt", 900, 4), blob(inbox, "d.txt", 1500, 5),
                                  blob(inbox, "e.txt", 100, 6)], inbox)  # 1000 + 900 + 1500 > 3000; e still fits
    assert [(f.status, f.reason) for f in res2.files] == [
        ("indexed", ""), ("rejected", "over the 3 KB per-session total"), ("indexed", "")]
    _, res3 = sm.add_files(s.id, [blob(inbox, "f.txt", 100, 7)], inbox)
    assert [(f.status, f.reason) for f in res3.files] == [("rejected", "over the 4-file limit")]
    assert res3.report is None


def test_only_files_from_the_upload_folder_are_read(demo, inbox, tmp_path) -> None:
    sm = manager(demo, tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("not an upload", encoding="utf-8")
    _, res = sm.add_files(None, [outside, inbox / ".." / "secret.txt", tmp_path / "missing.txt"], inbox)
    assert [(f.status, f.reason) for f in res.files] == [
        ("rejected", "not an uploaded file"), ("rejected", "not an uploaded file"), ("rejected", "not found")]
    assert res.session_id is None and sm.sessions == {} and outside.exists()


def test_names_are_sanitised_and_kept_unique(demo, inbox, tmp_path) -> None:
    sm = manager(demo, tmp_path)
    a, b = inbox / "a", inbox / "b"
    a.mkdir()
    b.mkdir()
    (a / ".hidden notes.txt").write_text("first file about budgets", encoding="utf-8")
    (b / ".hidden notes.txt").write_text("second file about budgets", encoding="utf-8")
    s, res = sm.add_files(None, [a / ".hidden notes.txt", b / ".hidden notes.txt"], inbox)
    assert sorted(p.name for p in s.uploads.iterdir()) == ["hidden notes (2).txt", "hidden notes.txt"]
    assert [f.status for f in res.files] == ["indexed", "indexed"]


def test_gradio_copies_are_deleted_after_copying(demo, inbox, tmp_path) -> None:
    sm = manager(demo, tmp_path)
    files = make_files(inbox, 2)
    sm.add_files(None, files, inbox)
    assert not any(f.exists() for f in files)


def test_idle_sessions_expire_and_start_wipes_all(demo, inbox, tmp_path) -> None:
    clock = Clock()
    sm = manager(demo, tmp_path, clock=clock)
    s, _ = sm.add_files(None, make_files(inbox, 1), inbox)
    clock.t += 29 * 60
    assert sm.get(s.id) is not None  # touched: idle restarts
    clock.t += 29 * 60
    assert sm.expire() == []
    clock.t += 31 * 60
    assert sm.expire() == [s.id] and sm.get(s.id) is None and not s.dir.exists()
    s2, _ = sm.add_files(None, make_files(inbox, 1, start=5), inbox)
    restarted = manager(demo, tmp_path)
    restarted.wipe_all()
    assert not s2.dir.exists() and restarted.get(s2.id) is None


def test_session_ids_are_uuid4_and_unknown_ids_are_refused(demo, inbox, tmp_path) -> None:
    sm = manager(demo, tmp_path)
    s, _ = sm.add_files(None, make_files(inbox, 1), inbox)
    assert len(s.id) == 36 and s.id[14] == "4"
    for bad in ("../x", "", "0" * 36, None):
        assert sm.get(bad) is None
    assert sm.library(s.id) is s.library and sm.library("nope") is None


def test_session_cap(demo, inbox, tmp_path) -> None:
    sm = manager(demo, tmp_path, max_sessions=1)
    sm.add_files(None, make_files(inbox, 1), inbox)
    _, res = sm.add_files(None, make_files(inbox, 1, start=7), inbox)
    assert res.session_id is None and res.files[0].status == "rejected" and "busy" in res.files[0].reason


def test_uploaded_office_files_get_the_absolute_zip_cap(demo, inbox, tmp_path, make_docx) -> None:
    """Media compresses about 1:1, so only the upload-only cap on unpacked size stops this one (SPEC §6.3)."""
    import os

    from conftest import add_zip_member

    sm = manager(demo, tmp_path, upload_zip_max_total=1024 * 1024)
    d = inbox / "z"
    d.mkdir()
    big = add_zip_member(make_docx(d / "big.docx", ["A report with a large picture."]), "word/media/big.bin",
                         os.urandom(1_500_000))
    _, res = sm.add_files(None, [big], inbox)
    assert res.files[0].status == "error" and res.files[0].reason


def test_safe_name() -> None:
    from vault.session import safe_name

    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name(r"..\..\boot.ini") == "boot.ini"
    assert safe_name('a<b>c:"d|e?.png') == "a_b_c__d_e_.png"
    assert safe_name("...") == "upload"
    assert len(safe_name("x" * 300 + ".pdf")) == 124
