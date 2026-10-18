"""The Gradio app (SPEC §8): Search tab in both modes, Index tab in local mode, Upload tab in demo mode.

Handlers are plain functions over a `UiContext`, so tests call them without a browser. They take file ids from
the page, never paths.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import gradio as gr

from vault import db, platform
from vault.library import Library
from vault.search.parse import KIND_LABELS
from vault.ui import cards

log = logging.getLogger(__name__)

STYLES = (Path(__file__).parent / "styles.css").read_text(encoding="utf-8")

DEMO_EXAMPLES = ["error 0x80070005", "python traceback KeyError", "paper about denoising diffusion models",
                 "where is singular value decomposition defined", "slide about gradient descent",
                 "sunset at the beach", "whiteboard with equations", "dice on a table", "train ticket PNR"]
LOCAL_EXAMPLES = ["screenshot of an error message", "photo of a whiteboard", "invoice", "slides about"]

# Clicks inside the results go back to Python as ids; "Show more" is client-side (all 50 are in the page already).
RESULTS_JS = """
element.addEventListener('click', (e) => {
  const more = e.target.closest('.v-showmore');
  if (more) {
    element.querySelectorAll('.v-more').forEach((x) => { x.hidden = false; });
    more.remove();
    return;
  }
  const item = e.target.closest('[data-file]');
  if (!item) return;
  element.querySelectorAll('.v-selected').forEach((x) => x.classList.remove('v-selected'));
  item.classList.add('v-selected');
  const num = (v) => (v ? Number(v) : null);
  trigger('click', {file_id: num(item.dataset.file), chunk_id: num(item.dataset.chunk), page: num(item.dataset.page)});
});
"""

LOGO = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">'
        '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5 21 21"/></svg>')


@dataclass
class UiContext:
    mode: str  # "local" | "demo"
    library: Library  # local: your vault; demo: the read-only demo index
    page_results: int = 20
    examples: Sequence[str] = field(default_factory=list)

    @property
    def demo(self) -> bool:
        return self.mode == "demo"

    def target(self, session: str | None, scope: str | None) -> tuple[Library, list[str], str | None]:
        """The library to search, the roots to limit it to, and the session id image URLs need."""
        return self.library, [], None


def theme() -> gr.themes.Base:
    system = ["system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"]
    mono = ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"]
    return gr.themes.Base(primary_hue="indigo", neutral_hue="slate", radius_size="lg", font=system,
                          font_mono=mono).set(
        body_background_fill="#f4f6fb", body_background_fill_dark="#0e1218",
        block_background_fill="#ffffff", block_background_fill_dark="#161b24",
        block_border_color="#dfe4ee", block_border_color_dark="#2a3241",
        input_background_fill="#ffffff", input_background_fill_dark="#1d2430",
        button_primary_background_fill="#3352cc", button_primary_background_fill_hover="#2843ad",
        button_primary_background_fill_dark="#5b75e6", button_primary_background_fill_hover_dark="#7189f0",
        button_primary_text_color="#ffffff", button_primary_text_color_dark="#ffffff",
        button_secondary_background_fill="#ffffff", button_secondary_background_fill_dark="#1d2430",
        color_accent_soft="#e8edff", color_accent_soft_dark="#232c48",
    )


# ---------------------------------------------------------------- handlers


def intro_html(ctx: UiContext) -> str:
    what = ("the sample files: generated screenshots, photos, PDFs, decks and notes" if ctx.demo
            else "everything you've indexed")
    return (f'<div class="v-empty"><p class="v-empty-title">Search {html.escape(what)}</p>'
            "<p>Type what you remember: a phrase, an error code, what's in a picture. "
            "Add <b>screenshot</b>, <b>photo</b>, <b>pdf</b>, <b>slides</b> or <b>doc</b> to filter.</p></div>")


def run_search(ctx: UiContext, query: str, kinds: list[str] | None, scope: str | None, show_scores: bool,
               session: str | None) -> tuple[str, str]:
    """Search → (results HTML, the query text used for highlights)."""
    query = " ".join((query or "").split())
    if not query and not kinds:
        return intro_html(ctx), ""
    lib, roots, url_session = ctx.target(session, scope)
    try:
        resp = lib.search(query, kinds=kinds or (), roots=roots)
    except db.ModelMismatchError as e:
        return f'<div class="v-empty"><p class="v-empty-title">Index needs rebuilding</p><p>{html.escape(str(e))}</p></div>', ""
    note = scope if roots else ""
    return (cards.results_html(resp, url_session, lib.display, page=ctx.page_results, show_scores=show_scores,
                               scope_note=note), resp.parsed.text)


def _int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def show_detail(ctx: UiContext, query: str, scope: str | None, session: str | None, file_id: object,
                chunk_id: object, page: object) -> tuple[str, bool, int | None]:
    """A clicked card → (detail HTML, panel visible, selected file id)."""
    fid = _int(file_id)
    lib, _, url_session = ctx.target(session, scope)
    d = lib.detail(fid, _int(chunk_id), query or "") if fid is not None else None
    if d is None:
        return "", False, None
    return cards.detail_html(d, url_session, page=_int(page) or d.page), True, fid


def open_file(ctx: UiContext, file_id: int | None) -> str:
    """Local mode only: open the selected file in its default app. Returns a message for the user."""
    if ctx.demo or file_id is None:
        return "Nothing to open."
    path = ctx.library.file_path(file_id)
    if path is None:
        return "The file isn't at its indexed location any more. Run Index now to update."
    try:
        platform.open_file(path)
    except OSError as e:
        return f"Couldn't open it: {e}"
    return f"Opened {path.name}"


# ---------------------------------------------------------------- layout


def header_html(ctx: UiContext) -> str:
    notice = ""
    if ctx.demo:
        notice = ('<div class="v-notice"><strong>Demo on a cloud server.</strong> It searches generated sample '
                  "files, plus anything you upload for your session. The real thing runs on your own computer, "
                  "where your files and the models never leave the machine.</div>")
    return (f'<div class="v-brand"><div class="v-logo">{LOGO}</div><h1>Personal Search Vault</h1></div>'
            '<p class="v-tagline">Find your screenshots, photos, PDFs, slides and notes by what\'s in them. '
            f"Exact words, meaning and pictures, with local models.</p>{notice}")


def build_blocks(ctx: UiContext) -> gr.Blocks:
    with gr.Blocks(title="Personal Search Vault", analytics_enabled=False,
                   delete_cache=(300, 1800) if ctx.demo else None) as blocks:
        session = gr.State(None)  # demo: the upload session's id, kept server-side
        last_query = gr.State("")
        selected = gr.State(None)
        gr.HTML(header_html(ctx), elem_id="v-header")
        with gr.Tabs():
            with gr.Tab("Search", id="search"):
                with gr.Row(elem_id="v-searchbar"):
                    query = gr.Textbox(show_label=False, placeholder="Search your files… e.g. error 0x80070005",
                                       container=False, scale=8, autofocus=True, max_lines=1, elem_id="v-query")
                    go = gr.Button("Search", variant="primary", scale=0, elem_id="v-search-btn")
                with gr.Row(elem_id="v-filters"):
                    kinds = gr.CheckboxGroup([(label, kind) for kind, label in KIND_LABELS.items()], value=[],
                                             show_label=False, container=False, elem_id="v-kinds")
                    scope = gr.Radio(["My uploads", "Sample files", "Both"], value="Sample files", show_label=False,
                                     container=False, visible=False, elem_id="v-scope")
                    scores = gr.Checkbox(label="Show scores", value=False, container=False, scale=0, min_width=0,
                                         elem_id="v-scores")
                with gr.Row(elem_classes="v-examples"):
                    chips = [gr.Button(q, size="sm", elem_classes="v-chip", min_width=0) for q in ctx.examples]
                with gr.Row(elem_id="v-main", equal_height=False):
                    with gr.Column(scale=3, min_width=320):
                        results = gr.HTML(intro_html(ctx), js_on_load=RESULTS_JS, padding=False)
                    with gr.Column(scale=2, min_width=320, visible=False, elem_id="v-detail") as panel:
                        with gr.Row(elem_id="v-detail-actions"):
                            open_btn = gr.Button("Open file", size="sm", variant="primary", visible=not ctx.demo)
                            close_btn = gr.Button("Close", size="sm")
                        detail = gr.HTML(padding=False)

        search_inputs = [query, kinds, scope, scores, session]

        def do_search(q, k, sc, show, sid):
            out, used = run_search(ctx, q, k, sc, show, sid)
            return out, used, gr.update(visible=False), None

        search_outputs = [results, last_query, panel, selected]
        go.click(do_search, search_inputs, search_outputs, api_name="search")
        query.submit(do_search, search_inputs, search_outputs, api_name=False)
        kinds.change(do_search, search_inputs, search_outputs, api_name=False)
        scores.change(do_search, search_inputs, search_outputs, api_name=False)
        scope.change(do_search, search_inputs, search_outputs, api_name=False)
        for chip, q in zip(chips, ctx.examples):
            chip.click(lambda q=q: q, None, query, api_name=False).then(do_search, search_inputs, search_outputs,
                                                                         api_name=False)

        def on_click(lq, sc, sid, evt: gr.EventData):
            body, visible, fid = show_detail(ctx, lq, sc, sid, getattr(evt, "file_id", None),
                                             getattr(evt, "chunk_id", None), getattr(evt, "page", None))
            return body, gr.update(visible=visible), fid

        def on_load(k, sc, show, sid, request: gr.Request):
            """`?q=…` opens the page on a search (a link to share, or to check a deploy)."""
            q = " ".join(str(request.query_params.get("q", "") if request else "").split())[:200]
            if not q:
                return gr.update(), gr.update(), ""
            out, used = run_search(ctx, q, k, sc, show, sid)
            return q, out, used

        blocks.load(on_load, [kinds, scope, scores, session], [query, results, last_query], api_name=False)
        results.click(on_click, [last_query, scope, session], [detail, panel, selected], api_name=False)
        close_btn.click(lambda: (gr.update(visible=False), None), None, [panel, selected], api_name=False)

        def on_open(fid):
            gr.Info(open_file(ctx, fid))

        open_btn.click(on_open, selected, None, api_name=False)
        blocks.ctx = ctx  # for the server's id-only image routes
        blocks.session_state = session
    return blocks
