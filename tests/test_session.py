"""Demo uploads (SPEC §8): a per-session copy of the demo index, incremental uploads, limits enforced in code,
the expiry after the last search or upload, and a demo index that never changes."""

import hashlib
import json
import shutil
import subprocess
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
    """Every cap counts the whole session, over all its uploads, not one upload (owner, 19 Oct)."""
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
    ttl = sm.cfg.session_ttl_s
    s, _ = sm.add_files(None, make_files(inbox, 1), inbox)
    clock.t += ttl - 60
    assert sm.get(s.id) is not None  # a search: the idle time restarts
    clock.t += ttl - 60
    assert sm.expire() == []
    clock.t += 120
    assert sm.expire() == [s.id] and sm.get(s.id) is None and not s.dir.exists()
    s2, _ = sm.add_files(None, make_files(inbox, 1, start=5), inbox)
    restarted = manager(demo, tmp_path)
    restarted.wipe_all()
    assert not s2.dir.exists() and restarted.get(s2.id) is None


def test_only_a_search_or_an_upload_keeps_a_session_alive(demo, inbox, tmp_path) -> None:
    """Owner's rule (19 Oct): uploads are deleted `session_ttl_s` after the session's last search or upload, not
    after it started. The id-only routes (thumbnails, previews, downloads) look it up with `library()`, which
    doesn't count."""
    clock = Clock()
    sm = manager(demo, tmp_path, clock=clock)
    ttl = sm.cfg.session_ttl_s
    s, _ = sm.add_files(None, make_files(inbox, 1), inbox)
    clock.t += ttl - 60
    _, more = sm.add_files(s.id, make_files(inbox, 1, start=3), inbox)  # a second upload restarts it too
    assert more.session_id == s.id
    clock.t += ttl - 60
    assert sm.library(s.id) is s.library  # a thumbnail is still served…
    clock.t += 120
    assert sm.expire() == [s.id]  # …but didn't keep the session alive


def test_demo_limits_for_a_small_audience() -> None:
    """Owner's caps for the viva (19 Oct, §19): 5 sessions; per session 200 files over all its uploads, 500 MB in
    total, 50 MB per file and 500 MB unpacked per Office file; deleted 60 minutes after the last search or upload."""
    cfg = Config()
    assert cfg.max_sessions == 5 and cfg.upload_max_files == 200 and cfg.session_ttl_s == 60 * 60
    assert (cfg.upload_max_bytes, cfg.upload_max_total, cfg.upload_zip_max_total) == (50 * 2**20, 500 * 2**20,
                                                                                       500 * 2**20)


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


# ---------------------------------------------------------------- Delete my uploads (owner, 20 Oct: v1.0.4)


def test_delete_wipes_the_session_from_disk_and_the_index(demo, inbox, tmp_path) -> None:
    """The uploads, the session's copy of the index, its thumbnails and Gradio's copies (refused files stay in
    Gradio's folder until then) are gone at once, the slot is free, and the id no longer resolves."""
    sm = manager(demo, tmp_path)
    files = make_files(inbox, 3)
    refused = inbox / "r1" / "notes.zip"  # refused (type), so Gradio's copy stays
    refused.parent.mkdir()
    refused.write_bytes(b"PK not really")
    s, res = sm.add_files(None, [*files, refused], inbox)
    assert [f.status for f in res.files] == ["indexed"] * 3 + ["rejected"] and refused.exists()
    assert s.library.search("quarterly report").results and (s.dir / "vault.db").exists() and any(s.uploads.iterdir())
    assert sm.delete(s.id) is True
    assert not s.dir.exists() and sm.get(s.id) is None and sm.library(s.id) is None
    assert not refused.exists() and not refused.parent.exists()  # Gradio's copy and its now-empty folder
    assert all(not f.exists() for f in files) and not sm.sessions
    assert sm.delete(s.id) is None  # already gone


def test_delete_leaves_other_sessions_and_the_samples_alone(demo, inbox, tmp_path) -> None:
    sm = manager(demo, tmp_path)
    before = demo_digest(demo)
    a, _ = sm.add_files(None, make_files(inbox, 2), inbox)
    b, _ = sm.add_files(None, make_files(inbox, 2, start=10), inbox)
    assert sm.delete(a.id) is True
    assert sm.get(b.id) is b and b.dir.exists() and len(list(b.uploads.iterdir())) == 2
    assert {Path(p).name for r in b.library.search("quarterly report project 10").results for p in r.paths} >= \
        {"note 010.txt"}
    assert demo_digest(demo) == before and demo.search("diffusion").results


def test_delete_frees_the_slot_and_a_new_upload_starts_a_fresh_session(demo, inbox, tmp_path) -> None:
    sm = manager(demo, tmp_path, max_sessions=2)
    a, _ = sm.add_files(None, make_files(inbox, 1), inbox)
    sm.add_files(None, make_files(inbox, 1, start=5), inbox)
    assert sm.full
    assert sm.delete(a.id) is True and not sm.full
    c, res = sm.add_files(None, make_files(inbox, 1, start=9), inbox)
    assert c is not None and c.id != a.id and res.report.added == 1 and c.files == 1


def test_delete_refuses_ids_that_arent_a_live_session(demo, inbox, tmp_path) -> None:
    sm = manager(demo, tmp_path)
    s, _ = sm.add_files(None, make_files(inbox, 1), inbox)
    for bad in (None, "", "../" + s.id, s.id.upper(), "00000000-0000-4000-8000-000000000000", str(s.dir)):
        assert sm.delete(bad) is None
    assert sm.get(s.id) is s and s.dir.exists()


def test_delete_waits_for_an_upload_being_read(demo, inbox, tmp_path) -> None:
    """While one of its uploads is checked or read, nothing is touched (the caller is told to try later)."""
    sm = manager(demo, tmp_path)
    s, _ = sm.add_files(None, make_files(inbox, 1), inbox)
    s.busy = True
    assert sm.delete(s.id) is False and s.dir.exists() and sm.get(s.id) is s
    s.busy = False
    assert sm.delete(s.id) is True


def test_a_session_is_busy_from_the_start_of_an_upload(demo, inbox, tmp_path, monkeypatch) -> None:
    """A delete can't slip in while an upload is still being checked (hashing a large file takes a while)."""
    import vault.session as session_mod

    sm = manager(demo, tmp_path)
    s, _ = sm.add_files(None, make_files(inbox, 1), inbox)
    seen = []
    real_sha = session_mod.sha256_file
    monkeypatch.setattr(session_mod, "sha256_file", lambda p: (seen.append(sm.delete(s.id)), real_sha(p))[1])
    sm.add_files(s.id, make_files(inbox, 1, start=3), inbox)
    assert seen == [False] and not s.busy and sm.get(s.id) is s


def test_the_wipe_overwrites_files_before_removing_them(tmp_path) -> None:
    from vault.session import _shred

    f = tmp_path / "s" / "uploads" / "a.txt"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"secret" * 1000)
    _shred(tmp_path / "s")
    assert f.read_bytes() == b"\0" * 6000  # same size, zeros


def test_expiry_wipes_the_same_way(demo, inbox, tmp_path) -> None:
    clock = Clock()
    sm = manager(demo, tmp_path, clock=clock)
    refused = inbox / "r2" / "photo.heic.exe"
    refused.parent.mkdir()
    refused.write_bytes(b"x")
    s, _ = sm.add_files(None, [*make_files(inbox, 1), refused], inbox)
    clock.t += sm.cfg.session_ttl_s + 1
    assert sm.expire() == [s.id] and not s.dir.exists() and not refused.exists()


# ---------------------------------------------------------------- the cookie (owner, 20 Oct: v1.1.0)


def test_a_new_session_is_claimed_once_with_its_ticket(demo, inbox, tmp_path) -> None:
    """The first accepted upload creates the session with a one-time ticket; its page trades it for the cookie's
    token: at least 128 random bits, of which the server keeps only the hash. A used ticket is worth nothing."""
    import base64
    import secrets as _secrets

    sm = manager(demo, tmp_path)
    s, _ = sm.add_files(None, make_files(inbox, 1), inbox)
    ticket = sm.ticket(s.id)
    assert ticket and s.token is None and sm.by_token(ticket) is None  # the ticket isn't a cookie
    token = sm.claim(ticket)
    assert token and len(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))) * 8 >= 128
    assert sm.by_token(token) == s.id and s.token == hashlib.sha256(token.encode()).hexdigest()
    assert token not in repr(vars(s)) and ticket not in repr(vars(s))  # neither is kept on the session
    assert sm.claim(ticket) is None and sm.ticket(s.id) is None  # once only
    assert sm.claim(_secrets.token_urlsafe(32)) is None and sm.claim("") is None and sm.claim(None) is None
    _, more = sm.add_files(s.id, make_files(inbox, 1, start=3), inbox)  # a later upload makes no new ticket
    assert more.session_id == s.id and sm.ticket(s.id) is None and sm.by_token(token) == s.id


def test_an_unclaimed_ticket_expires(demo, inbox, tmp_path) -> None:
    from vault.session import CLAIM_TTL_S

    clock = Clock()
    sm = manager(demo, tmp_path, clock=clock)
    s, _ = sm.add_files(None, make_files(inbox, 1), inbox)
    ticket = sm.ticket(s.id)
    clock.t += CLAIM_TTL_S + 1
    assert sm.ticket(s.id) is None and sm.claim(ticket) is None and s.token is None


def test_forged_ended_and_deleted_tokens_name_no_session(demo, inbox, tmp_path) -> None:
    """A forged token names nothing and isn't "ended"; an expired session's token is remembered as ended (so the
    page can say its uploads were deleted after 60 minutes); a session its visitor deleted isn't. A restart forgets
    them all."""
    clock = Clock()
    sm = manager(demo, tmp_path, clock=clock)
    a, _ = sm.add_files(None, make_files(inbox, 1), inbox)
    b, _ = sm.add_files(None, make_files(inbox, 1, start=1), inbox)
    ta, tb = sm.claim(sm.ticket(a.id)), sm.claim(sm.ticket(b.id))
    assert sm.by_token(ta) == a.id and sm.by_token(tb) == b.id and ta != tb
    for forged in ("forged", ta[:-1], ta + "x", "x" * 5000, None, ""):
        assert sm.by_token(forged) is None and not sm.ended(forged)
    assert sm.delete(b.id) is True and sm.by_token(tb) is None and not sm.ended(tb)
    clock.t += sm.cfg.session_ttl_s + 1
    assert sm.expire() == [a.id] and sm.by_token(ta) is None and sm.ended(ta)
    sm.wipe_all()
    assert not sm.ended(ta)


# ---------------------------------------------------------------- the upload tray (owner, 20 Oct: v1.1.0)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_upload_trays_checks_mirror_the_server(demo, inbox, tmp_path) -> None:
    """The page's tray marks the files the server will refuse, before anything is sent, in the server's own words
    and order: the type (as Path.suffix reads it), the size, then the session's file count and total, counted over
    the files that will be added, after what the session already holds."""
    sm = manager(demo, tmp_path, upload_max_bytes=1000, upload_max_files=4, upload_max_total=2500)
    s, _ = sm.add_files(None, [blob(inbox, "first.txt", 400, 1)], inbox)
    plan = [("a.txt", 900), ("b.zip", 40), ("c.pdf", 1001), ("noext", 40), (".hidden", 40), ("d.MD", 700),
            ("e.png", 600), ("f.txt", 100), ("g.txt", 50), ("h.", 40)]
    files = [blob(inbox, name, size, 10 + i) for i, (name, size) in enumerate(plan)]
    limits = sm.limits(s.id)
    assert (limits["files"], limits["bytes"], limits["maxBytesText"], limits["maxTotalText"]) == (1, 400, "1000 bytes",
                                                                                                   "2 KB")
    shown = [{"name": f.name, "size": f.stat().st_size} for f in files]
    js = Path(__file__).resolve().parents[1] / "vault" / "ui" / "static" / "cortex.js"
    code = (f"const C = require({json.dumps(str(js))}); "
            f"process.stdout.write(JSON.stringify(C.trayCheck({json.dumps(shown)}, {json.dumps(limits)})));")
    tray = json.loads(subprocess.run([shutil.which("node"), "-e", code], capture_output=True, text=True,
                                     encoding="utf-8", check=True).stdout)
    _, result = sm.add_files(s.id, files, inbox)
    server = [f.reason if f.status == "rejected" else "" for f in result.files]
    assert tray == server
    assert [bool(r) for r in tray] == [False, True, True, True, True, False, True, False, True, True]
    assert tray[6] == "over the 2 KB per-session total" and tray[8] == "over the 4-file limit"
