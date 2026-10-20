"""Demo uploads (SPEC §8, §18): each visitor's uploads go into their own copy of the demo index.

The first upload copies the demo index (SQLite backup API) into `<root>/<uuid4>/` and indexes the files there;
later uploads add only their new files (the indexer's fast path skips the earlier ones, and a file identical to
one already uploaded is skipped by sha256). Limits are enforced here, not only by Gradio. Only files inside
Gradio's upload folder are read, and each is copied into the session and the Gradio copy deleted. A session is
created only by the first accepted upload, never by opening or searching the page, so crawlers can't use up the
`max_sessions` slots. It is wiped `session_ttl_s` after its last search or upload (fetching a thumbnail or a
preview doesn't count), and every session is wiped when the app starts.

A session belongs to a browser, not a tab (SPEC §8, v1.1): its cookie holds a random token (256 bits), of which the
server keeps only the SHA-256. Gradio's handlers can read cookies but not set them, so the upload that creates a
session gives its page a one-time ticket, which the page trades for the cookie (`claim`, the `/session/claim`
route). The session's folder is named by a uuid4 that never leaves the server.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from vault.config import Config
from vault.ingest import thumbs
from vault.ingest.discover import INDEX_EXTS, sha256_file
from vault.ingest.pipeline import IndexReport
from vault.library import Library, ProgressFn

log = logging.getLogger(__name__)

DEFAULT_ROOT = Path(tempfile.gettempdir()) / "vault_sessions"
BUSY = "The demo is busy, try again in a few minutes"  # max_sessions upload sessions are open
COOKIE = "cv_session"  # HttpOnly, Secure, SameSite=Strict (vault/ui/server.py)
CLAIM_TTL_S = 300  # a new session's one-time ticket can be traded for its cookie this long
ENDED_KEEP_S = 24 * 3600  # an expired session's token is remembered this long, so its page can say why
ENDED_MAX = 1000
_TOKEN_MAX = 200  # longer cookie values aren't ours
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


@dataclass
class FileStatus:
    name: str
    status: str  # "indexed" | "duplicate" | "rejected" | "error"
    reason: str = ""


@dataclass
class UploadResult:
    session_id: str | None
    files: list[FileStatus]
    report: IndexReport | None = None  # None when nothing new was indexed
    busy: bool = False  # no session could be opened: max_sessions are in use


@dataclass
class UploadView:
    """The latest upload as the upload bar shows it, kept on the session so a reload or a second tab shows it too:
    its stage ("list", "read", "done"), real counts, and the per-file outcome once listed."""

    id: str
    phase: str = "list"
    done: int = 0
    total: int = 0
    files: list[FileStatus] | None = None
    received: int = 0


@dataclass
class Session:
    id: str
    dir: Path
    library: Library
    last_used: float
    shas: set[str] = field(default_factory=set)  # every file uploaded so far
    files: int = 0
    bytes: int = 0
    busy: bool = False  # an upload is being checked or read: never wiped mid-run
    received: set[Path] = field(default_factory=set)  # Gradio's copies of its uploads (refused ones stay there)
    upload_dir: Path | None = None  # Gradio's upload folder they came from
    claim: str | None = None  # the one-time ticket its page trades for the cookie; None once used
    claim_until: float = 0.0
    token: str | None = None  # SHA-256 of the cookie's token, once claimed
    upload: UploadView | None = None  # the latest upload, for the bar after a reload

    @property
    def uploads(self) -> Path:
        return self.dir / "uploads"

    @property
    def root(self) -> str:
        """The indexed root of this session's uploads: the *My uploads* scope."""
        return str(self.uploads.resolve())


def human_bytes(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.0f} GB"


def safe_name(name: str) -> str:
    """A plain filename: no folders, no control or reserved characters, not hidden, not too long."""
    name = _UNSAFE.sub("_", Path(name.replace("\\", "/")).name).strip(" .") or "upload"
    stem, suffix = Path(name).stem, Path(name).suffix
    return stem[:120] + suffix[:10]


def _shred(path: Path) -> None:
    """Overwrite a file with zeros, in place, before it's removed (best effort: flash storage and copy-on-write or
    journaling filesystems may still keep old blocks). A folder: every file in it."""
    files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()] if path.is_dir() else []
    zeros = b"\0" * (1 << 20)
    for f in files:
        if f.is_symlink():
            continue
        try:
            left = f.stat().st_size
            with f.open("r+b") as out:
                while left > 0:
                    left -= out.write(zeros[:min(left, len(zeros))])
                out.flush()
                os.fsync(out.fileno())
        except OSError as e:  # removed anyway
            log.warning("could not overwrite %s: %s", f.name, e)


def file_id(value: object) -> int | None:
    """A file id from page data: a whole number above 0 that SQLite can hold (a larger one would make it raise),
    as an int or a string of digits; anything else is None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 19:
        value = int(value)
    return value if isinstance(value, int) and 0 < value < 2**63 else None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


def _unique(folder: Path, name: str) -> Path:
    p = folder / name
    n = 2
    while p.exists():
        p = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return p


class SessionManager:
    def __init__(self, demo: Library, cfg: Config, root: Path = DEFAULT_ROOT,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.demo = demo
        self.cfg = cfg
        self.root = root
        self.clock = clock
        self.sessions: dict[str, Session] = {}
        self._ended: dict[str, float] = {}  # token hash → when its session expired
        self._lock = threading.Lock()

    # ------------------------------------------------------------ lifecycle

    def wipe_all(self) -> None:
        """At app start: nothing from an earlier run survives."""
        with self._lock:
            self.sessions.clear()
            self._ended.clear()
            if self.root.exists():
                shutil.rmtree(self.root, ignore_errors=True)

    def get(self, session_id: str | None, touch: bool = True) -> Session | None:
        """The live session with this id. `touch`: this is a search or an upload, so its idle time restarts."""
        if not session_id or not _UUID4.match(session_id):
            return None
        with self._lock:
            s = self.sessions.get(session_id)
            if s is not None and touch:
                s.last_used = self.clock()
            return s

    @property
    def full(self) -> bool:
        """No new upload session can open now (a visitor who has one can still add to it)."""
        with self._lock:
            return len(self.sessions) >= self.cfg.max_sessions

    def library(self, session_id: str | None) -> Library | None:
        """For the id-only routes (thumbnails, previews, downloads): these don't keep a session alive."""
        s = self.get(session_id, touch=False)
        return s.library if s else None

    def expire(self) -> list[str]:
        """Wipe sessions whose last search or upload is older than the TTL; returns their ids."""
        now = self.clock()
        with self._lock:
            old = [s for s in self.sessions.values() if not s.busy and now - s.last_used > self.cfg.session_ttl_s]
            for s in old:
                del self.sessions[s.id]
                if s.token:
                    self._ended[s.token] = now
            if old:
                kept = sorted(((t, h) for h, t in self._ended.items() if now - t < ENDED_KEEP_S), reverse=True)
                self._ended = {h: t for t, h in kept[:ENDED_MAX]}
        for s in old:
            self._wipe(s)
            log.info("session %s expired", s.id[:8])
        return [s.id for s in old]

    # ------------------------------------------------------------ the cookie

    def by_token(self, token: str | None) -> str | None:
        """The id of the live session this cookie's token names, or None: no cookie, a forged or stale one, or a
        session that has ended."""
        if not token or len(token) > _TOKEN_MAX:
            return None
        h = token_hash(token)
        with self._lock:
            for s in self.sessions.values():
                if s.token is not None and hmac.compare_digest(s.token, h):
                    return s.id
        return None

    def ended(self, token: str | None) -> bool:
        """Whether this token named a session that expired (not one deleted by its visitor, nor a forged token)."""
        if not token or len(token) > _TOKEN_MAX:
            return False
        with self._lock:
            return token_hash(token) in self._ended

    def claim(self, ticket: str | None) -> str | None:
        """Trade a new session's one-time ticket for its cookie's token: fresh random bytes (256 bits) whose hash the
        session keeps. None for a ticket that is unknown, used already, or older than CLAIM_TTL_S."""
        if not ticket or len(ticket) > _TOKEN_MAX:
            return None
        now = self.clock()
        with self._lock:
            for s in self.sessions.values():
                if s.claim is not None and hmac.compare_digest(s.claim, ticket):
                    s.claim = None  # used, whatever happens next
                    if now > s.claim_until:
                        return None
                    token = secrets.token_urlsafe(32)
                    s.token = token_hash(token)
                    return token
        return None

    def limits(self, session_id: str | None) -> dict:
        """What the page's tray checks before anything is sent, mirroring `_add` (SPEC §8): the extensions taken, the
        per-file and per-session caps with the words the server uses for them, and what this session holds so far.
        The server still checks every file it's sent."""
        s = self.get(session_id, touch=False)
        cfg = self.cfg
        return {"exts": sorted(INDEX_EXTS), "maxBytes": cfg.upload_max_bytes,
                "maxBytesText": human_bytes(cfg.upload_max_bytes), "maxFiles": cfg.upload_max_files,
                "maxTotal": cfg.upload_max_total, "maxTotalText": human_bytes(cfg.upload_max_total),
                "files": s.files if s else 0, "bytes": s.bytes if s else 0}

    def ticket(self, session_id: str | None) -> str | None:
        """The session's unused, unexpired ticket, for its own page only (the upload that created it)."""
        s = self.get(session_id, touch=False)
        if s is None or s.claim is None or self.clock() > s.claim_until:
            return None
        return s.claim

    def delete(self, session_id: str | None) -> bool | None:
        """Delete my uploads: wipe this session now, the way expiry does, and free its slot. None when there's no
        such live session; False while one of its uploads is being checked or read (nothing is touched then)."""
        if not session_id or not _UUID4.match(session_id):
            return None
        with self._lock:
            s = self.sessions.get(session_id)
            if s is None:
                return None
            if s.busy:
                return False
            del self.sessions[session_id]
        self._wipe(s)
        log.info("session %s deleted by its visitor", s.id[:8])
        return True

    def uploads(self, session_id: str | None) -> list[dict]:
        """Manage uploads: this session's own uploaded files (newest first), by their id in its index, with name,
        size and date. Never the samples, never another session's."""
        s = self.get(session_id, touch=False)
        if s is None:
            return []
        with s.library.connect() as conn:
            rows = conn.execute("SELECT f.id, p.path, f.indexed_at FROM paths p JOIN files f ON f.id = p.file_id "
                                "WHERE p.root = ? ORDER BY f.id DESC", (s.root,)).fetchall()
        out = []
        for file_id, stored, when in rows:
            path = Path(stored)
            if path.is_file() and path.resolve().is_relative_to(s.uploads.resolve()):
                out.append({"id": file_id, "name": path.name, "size": path.stat().st_size, "added": when or ""})
        return out

    def remove(self, session_id: str | None, file_ids: Sequence[object]) -> list[int] | bool | None:
        """Manage uploads, Delete selected: delete some of this session's own uploads by their id in its index. Each
        file is overwritten with zeros and removed, with its thumbnail, then the indexer's sweep forgets it
        (`Library.remove_files`: one transaction per file, FTS rows explicitly, a compaction). Ids that aren't this
        session's uploads are ignored. None: no such live session; False: one of its uploads is being checked or read
        (nothing is touched), as for `delete`. Returns the ids removed. The session's idle time doesn't restart."""
        if not session_id or not _UUID4.match(session_id):
            return None
        wanted = sorted({n for i in list(file_ids)[:1000] if (n := file_id(i)) is not None})
        with self._lock:
            s = self.sessions.get(session_id)
            if s is None:
                return None
            if s.busy:
                return False
            s.busy = True  # no upload, delete or expiry while files are removed
        try:
            return self._remove(s, wanted)
        finally:
            s.busy = False

    def _remove(self, s: Session, wanted: list[int]) -> list[int]:
        if not wanted:
            return []
        marks = ",".join("?" * len(wanted))
        with s.library.connect() as conn:
            rows = conn.execute(f"SELECT f.id, p.path, f.sha256 FROM paths p JOIN files f ON f.id = p.file_id "
                                f"WHERE p.root = ? AND f.id IN ({marks})", (s.root, *wanted)).fetchall()
        uploads = s.uploads.resolve()
        gone: list[tuple[int, str, str, int]] = []
        for file_id, stored, sha in rows:
            path = Path(stored)
            if not path.resolve().is_relative_to(uploads):
                continue
            size = path.stat().st_size if path.is_file() else 0
            _shred(path)
            path.unlink(missing_ok=True)
            thumb = s.dir / thumbs.rel_path(sha)
            if thumb.is_file():
                _shred(thumb)  # the sweep removes it
            gone.append((file_id, stored, sha, size))
        s.library.remove_files([stored for _, stored, _, _ in gone])
        for _, _, sha, size in gone:
            s.shas.discard(sha)  # the same file can be uploaded again
            s.files -= 1
            s.bytes -= size
        log.info("session %s: %d upload(s) deleted by its visitor", s.id[:8], len(gone))
        return sorted({file_id for file_id, _, _, _ in gone})

    def _wipe(self, s: Session) -> None:
        """A session off the disk: every file overwritten with zeros, then its folder removed (the uploads, its copy
        of the index, its thumbnails), then any of Gradio's copies of its uploads still in Gradio's folder (refused
        files: accepted ones were removed when they were copied) and their now-empty upload folders."""
        _shred(s.dir)
        shutil.rmtree(s.dir, ignore_errors=True)
        for p in s.received:
            if p.is_file():
                _shred(p)
                p.unlink(missing_ok=True)
            if s.upload_dir and p.parent != s.upload_dir and p.parent.is_relative_to(s.upload_dir):
                try:
                    p.parent.rmdir()  # Gradio's per-upload folder, once empty
                except OSError:
                    pass

    def start_reaper(self, interval_s: float = 60.0) -> threading.Thread:
        def loop() -> None:
            while True:
                time.sleep(interval_s)
                try:
                    self.expire()
                except Exception:  # keep reaping whatever one wipe did
                    log.exception("session expiry failed")

        t = threading.Thread(target=loop, name="session-reaper", daemon=True)
        t.start()
        return t

    def _create(self) -> Session | None:
        with self._lock:
            if len(self.sessions) >= self.cfg.max_sessions:
                return None
            sid = str(uuid.uuid4())
            folder = self.root / sid
            (folder / "uploads").mkdir(parents=True)
            src = sqlite3.connect(f"{self.demo.db_path.resolve().as_uri()}?mode=ro", uri=True)
            dst = sqlite3.connect(folder / "vault.db")
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
            cfg = replace(self.cfg, data_dir=folder, zip_max_total=self.cfg.upload_zip_max_total)
            lib = Library(cfg, thumb_dirs=self.demo.thumb_dirs, base_dir=self.demo.base_dir, display_root=folder)
            s = Session(sid, folder, lib, self.clock(), busy=True,  # its first upload is being read
                        claim=secrets.token_urlsafe(32), claim_until=self.clock() + CLAIM_TTL_S)
            self.sessions[sid] = s
        log.info("session %s created", sid[:8])
        return s

    # ------------------------------------------------------------ uploads

    def add_files(self, session_id: str | None, files: Sequence[Path | str], upload_dir: Path,
                  progress: ProgressFn | None = None,
                  on_listed: Callable[[UploadResult], None] | None = None,
                  on_created: Callable[[Session], None] | None = None) -> tuple[Session | None, UploadResult]:
        """Check, copy and index uploaded files. `upload_dir` is Gradio's upload folder: nothing outside it is
        read. Creates the session on the first accepted file (`on_created` gets it then, before its files are
        read). `on_listed` gets the per-file outcome as soon as phase 1 has listed the files (SPEC §6.1); the
        returned result is the final one."""
        session = self._hold(session_id)  # busy from the start: Delete my uploads and the expiry wait for it
        held = [session] if session else []  # the busy session(s): this one, or the one created on the way
        try:
            return self._add(session, files, upload_dir.resolve(), progress, on_listed, held, on_created)
        finally:
            for s in held:
                s.busy = False
                s.last_used = self.clock()

    def _hold(self, session_id: str | None) -> Session | None:
        """The live session, marked busy at once (under the lock, so a delete can't slip in between)."""
        if not session_id or not _UUID4.match(session_id):
            return None
        with self._lock:
            s = self.sessions.get(session_id)
            if s is not None:
                s.busy = True
                s.last_used = self.clock()
            return s

    def _add(self, session: Session | None, files: Sequence[Path | str], upload_dir: Path,
             progress: ProgressFn | None, on_listed: Callable[[UploadResult], None] | None, held: list[Session],
             on_created: Callable[[Session], None] | None = None) -> tuple[Session | None, UploadResult]:
        statuses: list[FileStatus] = []
        accepted: list[tuple[Path, str, int, FileStatus]] = []
        received: list[Path] = []  # Gradio's copies this upload was given: a wipe removes any still there
        count = session.files if session else 0
        total = session.bytes if session else 0
        seen = set(session.shas) if session else set()
        for f in files:
            src = Path(f)
            st = FileStatus(safe_name(src.name), "rejected")
            statuses.append(st)
            try:
                real = src.resolve(strict=True)
            except OSError:
                st.reason = "not found"
                continue
            if not real.is_relative_to(upload_dir) or not real.is_file():
                st.reason = "not an uploaded file"
                continue
            received.append(real)
            if real.suffix.lower() not in INDEX_EXTS:
                st.reason = f"file type not supported ({real.suffix or 'no extension'})"
                continue
            size = real.stat().st_size
            if size > self.cfg.upload_max_bytes:
                st.reason = f"larger than {human_bytes(self.cfg.upload_max_bytes)}"
                continue
            sha = sha256_file(real)
            if sha in seen:
                st.status, st.reason = "duplicate", "already uploaded"
                continue
            if count >= self.cfg.upload_max_files:
                st.reason = f"over the {self.cfg.upload_max_files}-file limit"
                continue
            if total + size > self.cfg.upload_max_total:
                st.reason = f"over the {human_bytes(self.cfg.upload_max_total)} per-session total"
                continue
            seen.add(sha)
            count += 1
            total += size
            accepted.append((real, sha, size, st))

        if session is not None:
            session.received.update(received)
            session.upload_dir = upload_dir
        if not accepted:
            return session, UploadResult(session.id if session else None, statuses)
        if session is None:
            session = self._create()
            if session is None:  # max_sessions are open: nothing is kept, Gradio's copies included
                for real, _, _, st in accepted:
                    st.reason = BUSY
                    real.unlink(missing_ok=True)
                return None, UploadResult(None, statuses, busy=True)
            held.append(session)  # created busy; add_files releases it
            session.received.update(received)
            session.upload_dir = upload_dir
            if on_created:
                on_created(session)

        copied: list[tuple[Path, FileStatus]] = []
        for real, sha, size, st in accepted:
            dest = _unique(session.uploads, st.name)
            shutil.copy2(real, dest)
            real.unlink(missing_ok=True)  # Gradio's copy: the session's is the only one now
            st.name = dest.name
            session.shas.add(sha)
            session.files += 1
            session.bytes += size
            copied.append((dest, st))

        def outcome() -> None:
            with session.library.connect() as conn:
                for dest, st in copied:
                    row = conn.execute("SELECT f.status, f.error FROM paths p JOIN files f ON f.id = p.file_id "
                                       "WHERE p.path = ?", (str(dest.resolve()),)).fetchone()
                    if row is None:
                        st.status, st.reason = "error", "not indexed"
                    elif row["status"] == "ok":
                        st.status, st.reason = "indexed", ""
                    else:
                        st.status, st.reason = "error", row["error"] or row["status"]

        def listed() -> None:
            outcome()
            if on_listed:
                on_listed(UploadResult(session.id, list(statuses)))

        report = session.library.index([session.uploads], progress=progress, listed=listed)
        outcome()
        return session, UploadResult(session.id, statuses, report)
