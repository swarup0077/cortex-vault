"""Demo uploads (SPEC §8, §18): each visitor's uploads go into their own copy of the demo index.

The first upload copies the demo index (SQLite backup API) into `<root>/<uuid4>/` and indexes the files there;
later uploads add only their new files (the indexer's fast path skips the earlier ones, and a file identical to
one already uploaded is skipped by sha256). Limits are enforced here, not only by Gradio. Only files inside
Gradio's upload folder are read, and each is copied into the session and the Gradio copy deleted. A session idle
for `session_ttl_s` is wiped, and every session is wiped when the app starts.
"""

from __future__ import annotations

import logging
import re
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
from vault.ingest.discover import INDEX_EXTS, sha256_file
from vault.ingest.pipeline import IndexReport
from vault.library import Library, ProgressFn

log = logging.getLogger(__name__)

DEFAULT_ROOT = Path(tempfile.gettempdir()) / "vault_sessions"
BUSY = "The demo is busy, try again in a few minutes"  # max_sessions upload sessions are open
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
class Session:
    id: str
    dir: Path
    library: Library
    last_used: float
    shas: set[str] = field(default_factory=set)  # every file uploaded so far
    files: int = 0
    bytes: int = 0
    busy: bool = False  # indexing: never expired mid-run

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
        self._lock = threading.Lock()

    # ------------------------------------------------------------ lifecycle

    def wipe_all(self) -> None:
        """At app start: nothing from an earlier run survives."""
        with self._lock:
            self.sessions.clear()
            if self.root.exists():
                shutil.rmtree(self.root, ignore_errors=True)

    def get(self, session_id: str | None) -> Session | None:
        if not session_id or not _UUID4.match(session_id):
            return None
        with self._lock:
            s = self.sessions.get(session_id)
            if s is not None:
                s.last_used = self.clock()
            return s

    @property
    def full(self) -> bool:
        """No new upload session can open now (a visitor who has one can still add to it)."""
        with self._lock:
            return len(self.sessions) >= self.cfg.max_sessions

    def library(self, session_id: str | None) -> Library | None:
        s = self.get(session_id)
        return s.library if s else None

    def expire(self) -> list[str]:
        """Wipe sessions idle longer than the TTL; returns their ids."""
        now = self.clock()
        with self._lock:
            old = [s for s in self.sessions.values() if not s.busy and now - s.last_used > self.cfg.session_ttl_s]
            for s in old:
                del self.sessions[s.id]
        for s in old:
            shutil.rmtree(s.dir, ignore_errors=True)
            log.info("session %s expired", s.id[:8])
        return [s.id for s in old]

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
            s = Session(sid, folder, lib, self.clock())
            self.sessions[sid] = s
        log.info("session %s created", sid[:8])
        return s

    # ------------------------------------------------------------ uploads

    def add_files(self, session_id: str | None, files: Sequence[Path | str], upload_dir: Path,
                  progress: ProgressFn | None = None,
                  on_listed: Callable[[UploadResult], None] | None = None) -> tuple[Session | None, UploadResult]:
        """Check, copy and index uploaded files. `upload_dir` is Gradio's upload folder: nothing outside it is
        read. Creates the session on the first accepted file. `on_listed` gets the per-file outcome as soon as
        phase 1 has listed the files (SPEC §6.1); the returned result is the final one."""
        session = self.get(session_id)
        upload_dir = upload_dir.resolve()
        statuses: list[FileStatus] = []
        accepted: list[tuple[Path, str, int, FileStatus]] = []
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

        if not accepted:
            return session, UploadResult(session.id if session else None, statuses)
        if session is None:
            session = self._create()
            if session is None:  # max_sessions are open: nothing is kept, Gradio's copies included
                for real, _, _, st in accepted:
                    st.reason = BUSY
                    real.unlink(missing_ok=True)
                return None, UploadResult(None, statuses, busy=True)

        session.busy = True
        try:
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
        finally:
            session.busy = False
            session.last_used = self.clock()
        return session, UploadResult(session.id, statuses, report)
