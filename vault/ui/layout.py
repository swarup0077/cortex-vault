"""The Gradio app (SPEC §8): one Cortex Vault screen in both modes.

The page is four gr.HTML parts built on the server by `vault.ui.views` (top: header, hero, search box and the
recent / example searches; status; main: The Archive beside home, the results or an upload outcome; footer) and
driven by `static/cortex.js`, which sends events back here with
`trigger`. Handlers are plain functions over a `UiContext`, so tests call them without a browser. They take file
ids and query text from the page, never paths. In demo mode the visitor's upload session is the one their browser's
cookie names (`cookie`, SPEC §8): never an id from the page or the URL. The local Folders panel and its handlers
exist only in local mode.
"""

from __future__ import annotations

import functools
import logging
import secrets
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import gradio as gr

from vault import db, platform
from vault.library import Library
from vault.session import COOKIE, SessionManager, UploadView
from vault.ui import views

log = logging.getLogger(__name__)

SCOPES = ("Samples", "My uploads", "Both")
MAX_QUERY = 200



def _when_ready(call: str) -> str:
    """Run `call` once static/cortex.js has loaded: Gradio inserts <head> scripts itself, so they load
    asynchronously and a part can mount first."""
    return ("(function go(n) { if (window.Cortex) { " + call + " } else if (n < 400) "
            "{ setTimeout(function () { go(n + 1); }, 25); } })(0);")


# Each part hands its element (and Gradio's trigger / upload functions) to static/cortex.js.
TOP_JS = _when_ready("Cortex.mountTop(element, trigger, typeof upload === 'function' ? upload : null);")
MAIN_JS = _when_ready("Cortex.mountMain(element, trigger);")
STATUS_JS = _when_ready("Cortex.mountStatus(element);")


def normalize(text: object) -> str:
    """The query as cortex.js normalises it (`Cortex.normalize`): whitespace collapsed, capped in length."""
    return " ".join(str(text or "").split())[:MAX_QUERY]


@dataclass
class UiContext:
    mode: str  # "local" | "demo"
    library: Library  # local: your vault; demo: the read-only demo index
    page_results: int = views.PAGE
    suggestions: Sequence[views.Suggestion] = ()
    sessions: SessionManager | None = None  # demo: per-session upload indexes
    upload_dir: Path | None = None  # Gradio's upload folder: the only place uploads are read from
    poll_s: float = 0.5  # how often an upload in progress reports its status
    view_mode: views.Mode = field(init=False)

    def __post_init__(self) -> None:
        cfg = self.sessions.cfg if self.sessions else self.library.cfg
        self.view_mode = views.Mode(demo=self.demo, ttl_min=max(1, round(cfg.session_ttl_s / 60)),
                                    max_files=cfg.upload_max_files)

    @property
    def demo(self) -> bool:
        return self.mode == "demo"

    @functools.cached_property
    def sample_roots(self) -> list[str]:
        with self.library.connect() as conn:
            return db.get_roots(conn)

    def session(self, session: str | None):
        """The live session with this id. Looking doesn't keep it alive: only a search or an upload does."""
        return self.sessions.get(session, touch=False) if self.sessions else None

    def sid(self, token: str | None) -> str | None:
        """The live session the cookie's token names, if any."""
        return self.sessions.by_token(token) if self.sessions else None

    def target(self, session: str | None, scope: str | None) -> tuple[Library, list[str]]:
        """The library to search and the roots to limit it to (the scope). Before any upload, or once a session has
        ended, that's the demo index itself."""
        s = self.session(session)
        if s is None:
            return self.library, []
        roots = {"My uploads": [s.root], "Samples": self.sample_roots}.get(scope or "", [])
        return s.library, roots

    def resolve(self, token: str | None) -> Library:
        """For the id-only routes: the session's copy of the index when the cookie names a live session, else the
        main index. A session's own files are reachable only with its cookie."""
        lib = self.sessions.library(self.sid(token)) if self.sessions else None
        return lib or self.library

    def place(self, session: str | None) -> views.Place:
        s = self.session(session)
        demo_root = self.sample_roots[0] if self.demo and self.sample_roots else None
        return views.place_for(demo_root, s.dir if s else None)


# ---------------------------------------------------------------- handlers


def cookie(request: gr.Request | None) -> str | None:
    """The session cookie's token on the request that started this event (Gradio hands handlers the queue's join
    request), or None."""
    inner = getattr(request, "request", None) if request is not None else None
    jar = getattr(inner, "cookies", None)
    if jar is None and request is not None:
        try:
            jar = request.cookies
        except AttributeError:
            jar = None
    value = jar.get(COOKIE) if jar is not None else None
    return value if isinstance(value, str) and value else None


def uploads_full(ctx: UiContext, session: str | None) -> bool:
    """A visitor without a session can't open one now; one who has a session can still add to it."""
    return bool(ctx.sessions) and ctx.session(session) is None and ctx.sessions.full


def status_html(ctx: UiContext, session: str | None, *, busy: tuple[str, int, int] | None = None,
                upload_id: str = "", files: Sequence | None = None, received: int = 0, message: str = "",
                new_session: bool = False, toast: str = "", forget: bool = False, reattach: bool = False) -> str:
    """The header status, the dial's state, the footer's index line, and the upload bar's server stages (SPEC §8):
    `busy` = (phase, done, total) while an upload is listed or read, with real counts (total 0: not known yet);
    `files` is its per-file outcome once listed; `message` says why nothing was kept. A new session's unused ticket
    goes to its page, which trades it for the cookie; `forget`: the page's cookie names no live session, so the page
    clears it; `reattach`: a page opened on a session that has uploads."""
    full = uploads_full(ctx, session)
    lib, _ = ctx.target(session, "Both")
    n = lib.count()
    if ctx.demo:
        index = f"{n} files · up to date"
    else:
        last = ctx.library.stats().last_indexed
        index = f"{n} files · " + (f"indexed {views.ago(last)}" if last else "not indexed yet")
    kw: dict = dict(index=index, version=lib.version(), uploads_full=full, upload_id=upload_id, message=message,
                    new_session=new_session, toast=toast, forget=forget, reattach=reattach,
                    claim=(ctx.sessions.ticket(session) if ctx.sessions else None) or "")
    if ctx.demo:  # The Archive's numbers for every scope: cortex.js repaints them as an upload is listed and read
        kw.update(counts={scope: scoped_counts(ctx, session, scope) for scope in SCOPES})
    if files is not None:
        kw.update(report=views.upload_report(files, ctx.view_mode, received=received),
                  summary=views.upload_summary(files))
    if busy is not None:
        phase, done, total = busy
        verb = "Reading" if phase == "read" else "Listing"
        dial = f"{verb} {min(done + 1, total)} of {total}" if total else verb
        return views.status(ctx.view_mode.status, dial_state=dial, stage=phase, done=done, total=total, **kw)
    return views.status(ctx.view_mode.status, stage="done" if upload_id and not message else "idle", **kw)


def page_status(ctx: UiContext, session: str | None, **kw) -> str:
    """The status for a page opened or reloaded on this session: its latest upload as the bar last showed it (the
    stage and counts while it's read, then the report)."""
    s = ctx.session(session)
    v = s.upload if s else None
    if v is None:
        return status_html(ctx, session, **kw)
    busy = (v.phase, v.done, v.total) if v.phase in ("list", "read") else None
    return status_html(ctx, session, busy=busy, upload_id=v.id, files=v.files, received=v.received, **kw)


def load_page(ctx: UiContext, token: str | None, params: object = None) -> Iterator[tuple[object, str]]:
    """Opening or reloading the page → yields (the main part, or gr.skip(); the status). A live session (the cookie)
    comes back with its uploads, counts and latest upload report; while one of its uploads is still being read, the
    bar follows it here too. A cookie that names no live session is cleared, and if its session expired the page
    says so. `?q=…&r=<id>` opens the page on a search with that result selected (Copy link)."""
    sid = ctx.sid(token)
    stale = bool(token) and sid is None
    toast = views.expired_text(ctx.view_mode.ttl_min) if stale and ctx.sessions and ctx.sessions.ended(token) else ""
    params = params if isinstance(params, dict) else {}
    q = normalize(params.get("q", ""))
    raw = str(params.get("r", ""))
    select = int(raw) if raw.isdigit() else None
    s = ctx.session(sid)
    main = run_search(ctx, sid, q, "Both", select) if q else home_html(ctx, sid)
    yield main, page_status(ctx, sid, toast=toast, forget=stale, reattach=bool(s and s.files))

    def reading() -> bool:
        return s.busy or (s.upload is not None and s.upload.phase in ("list", "read"))

    followed, idle = False, 0.0
    while s is not None and ctx.session(sid) is s and reading() and idle < 10:
        time.sleep(ctx.poll_s)
        idle = 0.0 if s.busy else idle + ctx.poll_s  # not busy but not done: the worker is writing its report
        followed = True
        yield gr.skip(), page_status(ctx, sid)
    if followed:
        yield gr.skip(), page_status(ctx, sid)


def scoped_counts(ctx: UiContext, session: str | None, scope: str) -> dict[str, int]:
    """Files per type in the scope (SPEC §8, owner 20 Oct): the samples, this session's uploads, or both in demo
    mode; the vault in local mode. My uploads before any upload is empty, not every sample."""
    if ctx.demo and scope == "My uploads" and ctx.session(session) is None:
        return dict.fromkeys(views.TYPES, 0)
    lib, roots = ctx.target(session, scope)
    return lib.type_counts(roots)


def archive(ctx: UiContext, session: str | None, scope: str = "Both") -> views.Archive:
    """The Archive's facts: files per type in the selected scope, as searches see it (SPEC §8)."""
    uploads = sum(scoped_counts(ctx, session, "My uploads").values()) if ctx.demo else 0
    return views.Archive(scoped_counts(ctx, session, scope), scope, uploads_full(ctx, session), uploads)


def delete_uploads(ctx: UiContext, session: str | None, query: object) -> tuple[str | None, str, str | None]:
    """Demo, Delete my uploads → (the main part, or None to leave it; the status; the session id). Only ever the
    caller's own session: the one its browser's cookie names, never an id a request sends. The session is wiped at
    once the way expiry does it (SessionManager.delete) and its slot is free; the view goes back to Samples, with
    the query on screen run again there, and the page clears the cookie."""
    q = normalize(query)
    n = sum(scoped_counts(ctx, session, "My uploads").values())
    done = ctx.sessions.delete(session) if ctx.sessions else None
    if done is False:  # one of its uploads is still being checked or read: nothing is touched
        return None, status_html(ctx, session, toast=views.DELETE_BUSY), session
    toast = views.deleted_text(n) if done else views.NOTHING_TO_DELETE
    return run_search(ctx, None, q, "Samples"), status_html(ctx, None, toast=toast, forget=True), None


def home_html(ctx: UiContext, session: str | None, scope: str = "Both") -> str:
    lib, roots = ctx.target(session, scope)
    folders = [] if ctx.demo else ctx.library.stats().roots
    return views.home(ctx.view_mode, archive(ctx, session, scope), recent=lib.recent_by_type(views.RECENT, roots),
                      folders=folders, samples=ctx.library.count() if ctx.demo else 0)


def run_search(ctx: UiContext, session: str | None, query: object, scope: object = "Both",
               select: int | None = None) -> str:
    """Search → the main part: home for an empty box, else the grouped results for exactly this text. A search
    keeps the session alive (SPEC §8)."""
    q = normalize(query)
    scope = scope if scope in SCOPES else "Both"
    if ctx.sessions and session:
        ctx.sessions.get(session)  # its idle time restarts
    if not q:
        return home_html(ctx, session, scope)
    if ctx.demo and scope == "My uploads" and ctx.session(session) is None:
        return views.nothing_uploaded(ctx.view_mode, archive(ctx, session, scope), q)
    lib, roots = ctx.target(session, scope)
    version = lib.version()  # before the search: results never claim to be newer than they are
    try:
        resp = lib.search(q, roots=roots)
    except db.ModelMismatchError as e:
        return views.needs_rebuild(ctx.view_mode, archive(ctx, session, scope), q, str(e))
    return views.results(resp, ctx.view_mode, ctx.place(session), archive(ctx, session, scope), query=q,
                         select=select, page=ctx.page_results, version=version)


def handle_upload(ctx: UiContext, session: str | None, paths: object) -> Iterator[tuple[str, str | None]]:
    """Demo uploads → yields (status, session id) while the files are listed and read: the upload bar's server
    stages with real counts, then the per-file report (SPEC §8). The main part is never an output, so results on
    screen don't move; the bar offers "Update results" instead. Only files in Gradio's upload folder are read; the
    session rejects everything else."""
    if not ctx.sessions or not isinstance(paths, list) or not paths:
        yield status_html(ctx, session), session
        return
    files = [Path(p) for p in paths[:1000] if isinstance(p, str)]
    upload_id = secrets.token_hex(6)
    progress = {"done": 0, "total": 0, "phase": "list"}
    outcome: dict = {"sid": session}

    def keep(phase: str | None = None) -> None:
        """The bar's state on the session too, for a reload or a second tab (`page_status`). Kept by the worker, so
        it stays right if this page goes away mid-upload."""
        s = ctx.session(outcome["sid"])
        if s is not None:
            done = outcome.get("listed")
            s.upload = UploadView(upload_id, phase or progress["phase"], progress["done"], progress["total"],
                                  list(done.files) if done else None, len(files))

    def report(done: int, total: int, path: str, phase: str = "read") -> None:
        progress.update(done=done, total=total, phase=phase)
        keep()

    def created(s) -> None:  # the first accepted file made the session: its page can claim the cookie now
        outcome["sid"] = s.id
        keep()

    def listed(result) -> None:  # phase 1 is done: names and native text are searchable, the report can show
        outcome["listed"] = result
        keep()

    def work() -> None:
        try:
            outcome["result"] = ctx.sessions.add_files(session, files, ctx.upload_dir, report, listed, created)
            _, result = outcome["result"]
            if result.session_id:
                outcome["listed"] = result
                keep("done")
        except Exception as e:  # said in the bar rather than a bar stuck on "Reading"
            log.exception("upload failed")
            outcome["error"] = e
            s = ctx.session(outcome["sid"])
            if s is not None:
                s.upload = None

    worker = threading.Thread(target=work, name="upload", daemon=True)
    keep("list")
    worker.start()
    yield status_html(ctx, session, busy=("list", 0, 0), upload_id=upload_id), session  # the server has the files
    while worker.is_alive():
        worker.join(ctx.poll_s)
        if not worker.is_alive():
            break
        done = outcome.get("listed")
        sid = outcome["sid"]
        yield status_html(ctx, sid, busy=(progress["phase"], progress["done"], progress["total"]),
                          upload_id=upload_id, files=done.files if done else None, received=len(files),
                          new_session=bool(sid) and not session), sid
    if "error" in outcome:
        yield status_html(ctx, session, upload_id=upload_id, message=views.UPLOAD_FAILED), session
        return
    _, result = outcome["result"]
    if result.busy:  # every session slot is taken: nothing was kept
        yield status_html(ctx, session, upload_id=upload_id,
                          message=f"{views.DEMO_BUSY}. {views.UPLOAD_KEPT_NONE}"), session
        return
    sid = result.session_id or session
    yield status_html(ctx, sid, upload_id=upload_id, files=result.files, received=len(files),
                      new_session=bool(sid) and not session), sid


def open_file(ctx: UiContext, file_id: object) -> str:
    """Local mode only: open a result in its default app. Returns a message for the user."""
    if ctx.demo:
        return "Nothing to open."
    try:
        fid = int(file_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "Nothing to open."
    path = ctx.library.file_path(fid)
    if path is None:
        return "The file isn't at its indexed location any more. Use Folders, then Index now, to update."
    try:
        platform.open_file(path)
    except OSError as e:
        return f"Couldn't open it: {e}"
    return f"Opened {path.name}"


def index_folder(ctx: UiContext, folder: str | None, progress: Callable[[int, int, str], None] | None = None) -> str:
    """Local mode: index one new folder, or every known folder when `folder` is None ("Index now")."""
    if ctx.demo:
        return ""
    if folder is not None:
        path = Path(folder.strip().strip('"')).expanduser()
        if not folder.strip() or not path.is_dir():
            return views.message(f"Not a folder: {folder}", ok=False)
        roots: list[Path | str] = [path]
    else:
        with ctx.library.connect() as conn:
            roots = list(db.get_roots(conn))
        if not roots:
            return views.message("Add a folder first.")
    try:
        rep = ctx.library.index(roots, progress=progress)
    except db.ModelMismatchError as e:
        return views.message(str(e), ok=False)
    parts = [f"{rep.added} added", f"{rep.linked} renamed or copied", f"{rep.updated} changed",
             f"{rep.files_deleted} removed", f"{len(rep.errors)} couldn't be read"]
    return views.message("Done: " + ", ".join(parts) + ".")


def remove_folder(ctx: UiContext, root: str | None) -> str:
    if ctx.demo or not root:
        return ""
    with ctx.library.connect() as conn:
        if root not in db.get_roots(conn):
            return views.message("Not an indexed folder.", ok=False)
    n = ctx.library.remove_root(root)
    return views.message(f"Removed {root} ({n} files left the index).")


def folders_state(ctx: UiContext) -> tuple[str, dict]:
    stats = ctx.library.stats()
    return views.folders_stats(stats), gr.update(choices=stats.roots, value=None)


def _progress_fn(progress: gr.Progress) -> Callable[..., None]:
    def report(done: int, total: int, path: str, phase: str = "read") -> None:
        verb = "Listing" if phase == "list" else "Reading"
        progress((done, total) if total else None, desc=f"{verb} {Path(path).name}" if path else "Finishing up…")
    return report


def _event(evt: gr.EventData | None, name: str, default: object = None) -> object:
    return getattr(evt, name, default) if evt is not None else default


# ---------------------------------------------------------------- layout


def build_blocks(ctx: UiContext) -> gr.Blocks:
    part = dict(padding=False, container=False, apply_default_css=False)
    with gr.Blocks(title="Cortex Vault", analytics_enabled=False,
                   delete_cache=(300, 1800) if ctx.demo else None) as blocks:
        top = gr.HTML(views.top(ctx.view_mode, ctx.suggestions), js_on_load=TOP_JS, elem_id="cv-top", **part)
        status = gr.HTML(status_html(ctx, None), js_on_load=STATUS_JS, elem_id="cv-status-src", **part)
        if not ctx.demo:  # the Folders panel and its handlers exist only in local mode (SPEC §8)
            with gr.Column(visible=False, elem_id="cv-folders") as folders:
                folder_stats = gr.HTML(**part)
                with gr.Row(elem_classes="cv-formrow"):
                    folder = gr.Textbox(label="Add a folder", placeholder=str(Path.home() / "Pictures"), scale=4)
                    add_btn = gr.Button("Add and index", scale=0)
                with gr.Row(elem_classes="cv-formrow"):
                    roots_dd = gr.Dropdown([], label="Indexed folders", scale=4)
                    remove_btn = gr.Button("Remove folder", scale=0)
                with gr.Row(elem_classes="cv-formrow"):
                    index_btn = gr.Button("Index now", variant="primary", scale=0)
                    close_btn = gr.Button("Close", scale=0)
                folder_msg = gr.HTML(**part)
        main = gr.HTML(home_html(ctx, None), js_on_load=MAIN_JS, elem_id="cv-main", **part)
        gr.HTML(views.footer(ctx.view_mode), elem_id="cv-foot", **part)

        def on_search(evt: gr.EventData, request: gr.Request):
            raw = str(_event(evt, "select", "") or "")  # "Update results" keeps the selected result
            return run_search(ctx, ctx.sid(cookie(request)), _event(evt, "q", ""), _event(evt, "scope", "Both"),
                              int(raw) if raw.isdigit() else None)

        top.submit(on_search, None, main, trigger_mode="always_last", concurrency_limit=8,
                   show_progress="hidden", api_name="search")

        def on_load(request: gr.Request):
            params = dict(request.query_params) if request else {}
            yield from load_page(ctx, cookie(request), params)

        # Not limited: a page whose session is still reading an upload follows it here, and mustn't hold others up
        blocks.load(on_load, None, [main, status], show_progress="hidden", api_name=False, concurrency_limit=None)

        if ctx.demo:  # uploads are read one job at a time, server-wide (Library.index holds a lock as well)
            def on_upload(evt: gr.EventData, request: gr.Request):
                for state, _ in handle_upload(ctx, ctx.sid(cookie(request)), _event(evt, "paths", [])):
                    yield state

            top.upload(on_upload, None, status, concurrency_limit=1, concurrency_id="index",
                       show_progress="hidden", api_name=False)

            def on_delete(evt: gr.EventData, request: gr.Request):
                """Delete my uploads: the session its browser's cookie names; nothing in the event names one."""
                if _event(evt, "action") != "delete-uploads":
                    return gr.skip(), gr.skip()
                page, state, _ = delete_uploads(ctx, ctx.sid(cookie(request)), _event(evt, "q", ""))
                return (gr.skip() if page is None else page), state

            main.click(on_delete, None, [main, status], concurrency_limit=4, show_progress="hidden",
                       api_name=False)
        else:
            def on_main_click(evt: gr.EventData):
                if _event(evt, "action") == "open":
                    message = open_file(ctx, _event(evt, "file_id"))
                    if not message.startswith("Opened"):
                        gr.Warning(message)

            main.click(on_main_click, None, None, show_progress="hidden", api_name=False)

            def on_top_click(evt: gr.EventData):
                if _event(evt, "action") == "folders":
                    return (gr.update(visible=True), *folders_state(ctx))
                return gr.skip(), gr.skip(), gr.skip()

            top.click(on_top_click, None, [folders, folder_stats, roots_dd], show_progress="hidden", api_name=False)

            def on_add(path, progress=gr.Progress()):
                return (index_folder(ctx, path, _progress_fn(progress)), *folders_state(ctx), status_html(ctx, None),
                        home_html(ctx, None))

            def on_index(progress=gr.Progress()):
                return (index_folder(ctx, None, _progress_fn(progress)), *folders_state(ctx), status_html(ctx, None),
                        home_html(ctx, None))

            def on_remove(root):
                return (remove_folder(ctx, root), *folders_state(ctx), status_html(ctx, None), home_html(ctx, None))

            outputs = [folder_msg, folder_stats, roots_dd, status, main]
            add_btn.click(on_add, folder, outputs, concurrency_limit=1, concurrency_id="index", api_name=False)
            index_btn.click(on_index, None, outputs, concurrency_limit=1, concurrency_id="index", api_name=False)
            remove_btn.click(on_remove, roots_dd, outputs, concurrency_limit=1, concurrency_id="index",
                             api_name=False)
            close_btn.click(lambda: gr.update(visible=False), None, folders, api_name=False)
    return blocks
