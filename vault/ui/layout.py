"""The Gradio app (SPEC §8): one Cortex Vault screen in both modes.

The page is four gr.HTML parts built on the server by `vault.ui.views` (top: header, hero, search box and the
recent / example searches; status; main: The Archive beside home, the results or an upload outcome; footer) and
driven by `static/cortex.js`, which sends events back here with
`trigger`. Handlers are plain functions over a `UiContext`, so tests call them without a browser. They take file
ids and query text from the page, never paths. The local Folders panel and its handlers exist only in local mode.
"""

from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import gradio as gr

from vault import db, platform
from vault.library import Library
from vault.session import SessionManager
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
        ttl_s = self.sessions.cfg.session_ttl_s if self.sessions else self.library.cfg.session_ttl_s
        self.view_mode = views.Mode(demo=self.demo, ttl_min=max(1, round(ttl_s / 60)))

    @property
    def demo(self) -> bool:
        return self.mode == "demo"

    @functools.cached_property
    def sample_roots(self) -> list[str]:
        with self.library.connect() as conn:
            return db.get_roots(conn)

    def session(self, session: str | None):
        return self.sessions.get(session) if self.sessions else None

    def target(self, session: str | None, scope: str | None) -> tuple[Library, list[str], str | None]:
        """The library to search, the roots to limit it to (the scope), and the session id image URLs need.
        Before any upload, or once a session has expired, that's the demo index itself."""
        s = self.session(session)
        if s is None:
            return self.library, [], None
        roots = {"My uploads": [s.root], "Samples": self.sample_roots}.get(scope or "", [])
        return s.library, roots, s.id

    def resolve(self, session: str | None) -> Library | None:
        """For the id-only routes: the main index, or a live session's copy."""
        if session is None:
            return self.library
        return self.sessions.library(session) if self.sessions else None

    def place(self, session: str | None) -> views.Place:
        s = self.session(session)
        demo_root = self.sample_roots[0] if self.demo and self.sample_roots else None
        return views.place_for(demo_root, s.dir if s else None)


# ---------------------------------------------------------------- handlers


def uploads_full(ctx: UiContext, session: str | None) -> bool:
    """A visitor without a session can't open one now; one who has a session can still add to it."""
    return bool(ctx.sessions) and ctx.session(session) is None and ctx.sessions.full


def status_html(ctx: UiContext, session: str | None, *, busy: tuple[int, int] | None = None) -> str:
    """The header status, the dial's state, the footer's index line and the upload progress (SPEC §8)."""
    full = uploads_full(ctx, session)
    lib, _, _ = ctx.target(session, "Both")
    n = lib.count()
    if ctx.demo:
        index = f"{n} files · up to date"
    else:
        last = ctx.library.stats().last_indexed
        index = f"{n} files · " + (f"indexed {views.ago(last)}" if last else "not indexed yet")
    if busy is not None:
        done, total = busy
        reading = f"Reading {min(done + 1, total)} of {total}"
        return views.status(f"{reading} uploads", index=index, dial_state=reading, busy=True, done=done, total=total,
                            uploads_full=full)
    return views.status(ctx.view_mode.status, index=index, uploads_full=full)


def archive(ctx: UiContext, session: str | None, scope: str = "Both") -> views.Archive:
    """The Archive's facts: files per type in everything this visitor can search (the samples plus this session's
    uploads in demo mode), whatever the scope (SPEC §8)."""
    lib, _, _ = ctx.target(session, "Both")
    return views.Archive(lib.type_counts(), scope, uploads_full(ctx, session))


def home_html(ctx: UiContext, session: str | None, scope: str = "Both") -> str:
    lib, roots, _ = ctx.target(session, scope)
    folders = [] if ctx.demo else ctx.library.stats().roots
    return views.home(ctx.view_mode, archive(ctx, session, scope), recent=lib.recent_by_type(views.RECENT, roots),
                      folders=folders, samples=ctx.library.count() if ctx.demo else 0)


def run_search(ctx: UiContext, session: str | None, query: object, scope: object = "Both",
               select: int | None = None) -> str:
    """Search → the main part: home for an empty box, else the grouped results for exactly this text."""
    q = normalize(query)
    scope = scope if scope in SCOPES else "Both"
    if not q:
        return home_html(ctx, session, scope)
    if ctx.demo and scope == "My uploads" and ctx.session(session) is None:
        return views.nothing_uploaded(ctx.view_mode, archive(ctx, session, scope), q)
    lib, roots, url_session = ctx.target(session, scope)
    try:
        resp = lib.search(q, roots=roots)
    except db.ModelMismatchError as e:
        return views.needs_rebuild(ctx.view_mode, archive(ctx, session, scope), q, str(e))
    return views.results(resp, ctx.view_mode, ctx.place(session), archive(ctx, session, scope), query=q,
                         session=url_session, select=select, page=ctx.page_results)


def handle_upload(ctx: UiContext, session: str | None, paths: object) -> Iterator[tuple[object, str, str | None]]:
    """Demo uploads → yields (main part or no change, status, session id) while the files are read, then the
    per-file outcome. Only files in Gradio's upload folder are read; the session rejects everything else."""
    if not ctx.sessions or not isinstance(paths, list) or not paths:
        yield gr.skip(), status_html(ctx, session), session
        return
    files = [Path(p) for p in paths[:1000] if isinstance(p, str)]
    progress = {"done": 0, "total": 0, "phase": "list"}
    outcome: dict = {}

    def report(done: int, total: int, path: str, phase: str = "read") -> None:
        progress.update(done=done, total=total, phase=phase)

    def listed(result) -> None:  # phase 1 is done: the outcome can show while phase 2 reads the files
        outcome["listed"] = result

    def work() -> None:
        try:
            outcome["result"] = ctx.sessions.add_files(session, files, ctx.upload_dir, report, listed)
        except Exception as e:  # shown as the outcome rather than a status stuck on "Reading"
            log.exception("upload failed")
            outcome["error"] = e

    worker = threading.Thread(target=work, name="upload", daemon=True)
    worker.start()
    shown = False
    while worker.is_alive():
        worker.join(ctx.poll_s)
        if not worker.is_alive():
            break
        main: object = gr.skip()
        sid = session
        if "listed" in outcome and not shown:  # the per-file outcome, as soon as the files are listed
            shown = True
            sid = outcome["listed"].session_id or session
            main = views.upload_outcome(outcome["listed"].files, ctx.view_mode,
                                        archive(ctx, sid, "My uploads" if sid else "Both"), received=len(files))
        elif shown:
            sid = outcome["listed"].session_id or session
        if progress["total"] and progress["phase"] == "read":
            yield main, status_html(ctx, sid, busy=(progress["done"], progress["total"])), sid
        elif shown:
            yield main, status_html(ctx, sid), sid
    if "error" in outcome:
        yield views.upload_failed(ctx.view_mode, archive(ctx, session)), status_html(ctx, session), session
        return
    _, result = outcome["result"]
    if result.busy:  # every session slot is taken: nothing was kept
        yield views.demo_busy(ctx.view_mode, archive(ctx, session)), status_html(ctx, session), session
        return
    sid = result.session_id or session
    main = views.upload_outcome(result.files, ctx.view_mode, archive(ctx, sid, "My uploads" if sid else "Both"),
                                received=len(files))
    yield main, status_html(ctx, sid), sid


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
        session = gr.State(None)  # demo: the upload session's id, kept server-side
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

        def on_search(sid, evt: gr.EventData):
            return run_search(ctx, sid, _event(evt, "q", ""), _event(evt, "scope", "Both"))

        top.submit(on_search, session, main, trigger_mode="always_last", concurrency_limit=8,
                   show_progress="hidden", api_name="search")

        def on_load(sid, request: gr.Request):
            """`?q=…&r=<id>` opens the page on a search with that result selected (Copy link)."""
            params = request.query_params if request else {}
            q = normalize(params.get("q", ""))
            raw = params.get("r", "")
            select = int(raw) if raw.isdigit() else None
            return (run_search(ctx, sid, q, "Both", select) if q else home_html(ctx, sid)), status_html(ctx, sid)

        blocks.load(on_load, session, [main, status], show_progress="hidden", api_name=False)

        if ctx.demo:  # uploads are read one job at a time, server-wide (Library.index holds a lock as well)
            def on_upload(sid, evt: gr.EventData):
                yield from handle_upload(ctx, sid, _event(evt, "paths", []))

            top.upload(on_upload, session, [main, status, session], concurrency_limit=1, concurrency_id="index",
                       show_progress="hidden", api_name=False)
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
