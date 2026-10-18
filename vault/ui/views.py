"""HTML for the Cortex Vault UI (SPEC §8, design/), built on the server for gr.HTML.

Pure functions from search results and index facts to HTML strings, so they're testable without a server. Every
file name, path, snippet and OCR string is escaped here (snippets arrive escaped already, with only `<mark>`
added); the only tags this module adds are the layout's. Images and downloads are referenced by file id, never
by path. `cortex.js` reads the `data-*` attributes and never builds HTML from them.
"""

from __future__ import annotations

import html
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode

from vault.library import Recent
from vault.search.engine import SearchResponse, SearchResult
from vault.search.parse import KIND_LABELS

PAGE = 20  # items shown before "Show N more" (SPEC §8)
UPLOAD_ROWS = 10  # upload outcome rows shown before "Show all N"

UPLOAD_NOTICE = ("Uploads are processed on this server, kept only for your session, and deleted after 30 minutes. "
                 "For private use, run it on your own computer (see README).")

# Chip order follows the design: All, Screenshots, Photos, PDFs, Slides, Docs.
CHIPS = ("screenshot", "photo", "pdf", "slides", "doc")
TYPE_NAMES = {"screenshot": "Screenshot", "photo": "Photo", "pdf": "PDF", "slides": "Slides", "doc": "Doc"}
METHODS = {"bm25": "exact words", "all_terms": "every word", "dense": "meaning", "clip": "what is in the picture"}
TEXT_EXTS = (".txt", ".md")

Place = Callable[[str], str]  # a stored path → where the UI says the file is ("Sample files / screenshots")


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _render() -> str:
    """A fresh token on each view's root, so cortex.js can tell a new render from its own edits."""
    return f'data-render="{secrets.token_hex(6)}"'


@dataclass(frozen=True)
class Mode:
    """What differs between the demo server and your own computer."""

    demo: bool
    where: str = field(init=False)  # "on this server" | "on this computer"

    def __post_init__(self) -> None:
        object.__setattr__(self, "where", "on this server" if self.demo else "on this computer")


# ---------------------------------------------------------------- small formatters


def human_bytes(n: int | None) -> str:
    if n is None:
        return ""
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("bytes", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def short_date(iso: str | None) -> str:
    try:
        dt = datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return ""
    return f"{dt.day} {dt:%b}" if dt else ""


def long_date(iso: str | None) -> str:
    try:
        dt = datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return ""
    if dt is None:
        return ""
    return f"{dt.day} {dt:%b %Y}" + (f", {dt:%H:%M}" if (dt.hour, dt.minute) != (0, 0) else "")


def ago(iso: str | None, now: datetime | None = None) -> str:
    """"2 min ago", "3 h ago", "today", or a date. A time ahead of now (an index built in another time zone)
    reads as today."""
    try:
        dt = datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return ""
    if dt is None:
        return ""
    now = now or datetime.now()
    secs = (now - dt).total_seconds()
    if dt.date() == now.date() or 0 > secs > -86400:
        if 0 <= secs < 3600:
            return "just now" if secs < 60 else f"{int(secs // 60)} min ago"
        return f"{int(secs // 3600)} h ago" if 0 <= secs < 6 * 3600 else "today"
    return f"{dt.day} {dt:%b}" + ("" if dt.year == now.year else f" {dt.year}")


def tile(kind: str, name: str) -> tuple[str, str]:
    """(tile tint, icon) for a file: plain text gets its own grey tile."""
    if kind == "doc" and Path(name).suffix.lower() in TEXT_EXTS:
        return "text", "text"
    return kind, kind


def type_name(kind: str, name: str) -> str:
    if kind == "doc" and Path(name).suffix.lower() in TEXT_EXTS:
        return "Text"
    return TYPE_NAMES.get(kind, kind)


def found_by(r: SearchResult) -> str:
    """"Exact words (rank 1), every word (rank 1) and meaning (rank 2)": each method's real rank. Empty when the
    search didn't report ranks (a filter-only search)."""
    parts = [f"{METHODS[m]} (rank {rank})" for m, rank in r.ranks.items() if rank and m in METHODS]
    if not parts:
        return ""
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return text[0].upper() + text[1:]


def text_read_by(r: SearchResult, mode: Mode) -> str:
    ocr = f"OCR, {mode.where}"
    by_source = {"ocr": ocr, "pdf_text": "The PDF's own text layer", "slide_text": "Slide text",
                 "doc_text": "Plain text" if Path(r.filename).suffix.lower() in TEXT_EXTS else "Word document text"}
    if r.chunk_source in by_source:
        return by_source[r.chunk_source]
    for source in ("ocr", "pdf_text", "slide_text", "doc_text"):
        if source in r.text_sources:
            return by_source[source]
    return "No text found"


def size_text(r: SearchResult) -> str:
    parts = []
    if r.kind in ("photo", "screenshot") and r.width and r.height:
        parts.append(f"{r.width} × {r.height}")
    elif r.kind in ("pdf", "slides") and r.pages:
        parts.append(f"{r.pages} {'slide' if r.kind == 'slides' else 'page'}{'s' if r.pages != 1 else ''}")
    if r.size_bytes is not None:
        parts.append(human_bytes(r.size_bytes))
    return " · ".join(parts)


def row_meta(r: SearchResult) -> str:
    kind = type_name(r.kind, r.filename)
    if r.page is not None:
        return f"{kind} · {'slide' if r.kind == 'slides' else 'page'} {r.page}"
    return " · ".join(p for p in (kind, short_date(r.effective_date)) if p)


def preview_meta(r: SearchResult, place: Place) -> str:
    kind = type_name(r.kind, r.filename)
    parts = [kind]
    if r.page is not None:
        unit = "slide" if r.kind == "slides" else "page"
        parts.append(f"{unit} {r.page}" + (f" of {r.pages}" if r.pages else ""))
    elif date := long_date(r.effective_date):
        parts.append(date)
    if r.paths:
        parts.append(place(r.paths[0]))
    return " · ".join(p for p in parts if p)


# ---------------------------------------------------------------- URLs (ids only, never paths)


def url(route: str, file_id: int, session: str | None = None, **params: object) -> str:
    query = {k: v for k, v in params.items() if v is not None and v != ""}
    if session:
        query["s"] = session
    return f"/{route}/{int(file_id)}" + (f"?{urlencode(query)}" if query else "")


def result_link(query: str, file_id: int) -> str:
    """Copy link to this result: the search and the file id, never a path."""
    return "?" + urlencode({"q": query, "r": int(file_id)})


# ---------------------------------------------------------------- page chrome


ICONS = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "upload": '<path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
    "folder": '<path d="M3 6a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',
    "screenshot": '<path d="M3 5h18v12H3zM8 21h8M12 17v4"/>',
    "pdf": '<path d="M6 2h9l5 5v15H6zM15 2v5h5M9 13h6M9 17h6"/>',
    "photo": '<path d="M3 4h18v16H3zM8.5 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3M21 16l-5-5-9 9"/>',
    "slides": '<path d="M3 4h18v12H3zM12 16v4M8 20h8M7 9h6"/>',
    "doc": '<path d="M6 2h9l5 5v15H6zM15 2v5h5M9 12h7M9 16h7"/>',
    "text": '<path d="M4 5h16v14H4zM8 10l3 2-3 2M13 15h4"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 17l-5-5-9 8"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "same": '<path d="M5 9h14M5 15h14"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
}
LOGO = ('<symbol id="cv-logo" viewBox="0 0 64 64"><rect width="64" height="64" rx="15" fill="#141416"/>'
        '<g transform="translate(5 5) scale(0.86)" fill="none" stroke="#D4A853" stroke-linecap="round">'
        '<circle cx="28" cy="28" r="16" stroke-width="5"/><path d="M41 41L53 53" stroke-width="6.5"/>'
        '<path d="M49.5 28H53M28 49.5V53M12.8 43.2L10.3 45.7M6.5 28H3M12.8 12.8L10.3 10.3M28 6.5V3M43.2 12.8L45.7 10.3" '
        'stroke-width="2.6"/></g><circle cx="29" cy="29" r="3" fill="#D4A853"/></symbol>')


def icon(name: str, cls: str = "cv-ico") -> str:
    return f'<svg class="{cls}" aria-hidden="true"><use href="#i-{name}"/></svg>'


def sprite() -> str:
    symbols = "".join(f'<symbol id="i-{k}" viewBox="0 0 24 24">{v}</symbol>' for k, v in ICONS.items())
    return f'<svg width="0" height="0" style="position:absolute" aria-hidden="true">{symbols}{LOGO}</svg>'


def top(mode: Mode) -> str:
    """Header and search bar: rendered once; cortex.js keeps them in step with the other parts."""
    scope = ("" if not mode.demo else
             '<div class="cv-seg" role="group" aria-label="Search in">'
             '<button type="button" aria-pressed="false" data-scope="Samples">Samples</button>'
             '<button type="button" aria-pressed="false" data-scope="My uploads">My uploads</button>'
             '<button type="button" aria-pressed="true" data-scope="Both">Both</button></div>')
    action = ('<button type="button" class="cv-btn-primary" data-act="add" aria-label="Add files">'
              f'{icon("plus")}<span class="cv-label">Add files</span></button>'
              '<input type="file" class="cv-sr" data-files multiple tabindex="-1" aria-hidden="true">'
              if mode.demo else
              '<button type="button" class="cv-btn-primary" data-act="folders" aria-label="Folders">'
              f'{icon("folder")}<span class="cv-label">Folders</span></button>')
    return (
        f'<div class="cv-app cv-part">{sprite()}'
        '<header class="cv-header">'
        '<a class="cv-brand" href="./" data-home><svg aria-hidden="true"><use href="#cv-logo"/></svg>Cortex Vault</a>'
        '<span class="cv-status" role="status" data-status><span class="cv-dot"></span><span data-status-text></span>'
        '</span>'
        f"{scope}{action}</header>"
        '<div class="cv-searchbar">'
        '<label class="cv-home__label" for="cv-q" data-home-label>Search by words, meaning, or what is in a picture'
        "</label>"
        '<div class="cv-search">' + icon("search") +
        '<input id="cv-q" type="search" placeholder="e.g. the slide about gradient descent" autocomplete="off" '
        'spellcheck="false" role="combobox" aria-expanded="false" aria-controls="cv-list" aria-autocomplete="list" '
        'aria-label="Search">'
        '<span class="cv-search__meta" data-meta></span><span class="cv-kbd">Ctrl K</span></div></div></div>'
    )


def footer(mode: Mode) -> str:
    line = ("Demo server · your uploads are deleted after 30 minutes" if mode.demo
            else "On this computer · your files and the models never leave it")
    return (f'<footer class="cv-app cv-part cv-footer"><span>{line}</span><span class="cv-keys">'
            "<span><b>Ctrl K</b> search</span><span><b>↑↓</b> select</span><span><b>Enter</b> open</span>"
            "</span></footer>")


def status(text: str, *, busy: bool = False, done: int = 0, total: int = 0) -> str:
    """The header's status line (copied into place by cortex.js) and, while uploads are read, the banner."""
    banner = ""
    if busy and total:
        left = total - done
        pct = round(100 * done / total)
        banner = (f'<div class="cv-banner">{left} of your uploads {"is" if left == 1 else "are"} still being read. '
                  f'Search keeps working meanwhile.<span class="cv-progress"><span style="width: {pct}%"></span>'
                  "</span></div>")
    return (f'<div class="cv-app cv-part" data-status-src data-busy="{str(busy).lower()}" data-text="{_e(text)}">'
            f"{banner}</div>")


# ---------------------------------------------------------------- home


@dataclass(frozen=True)
class Suggestion:
    query: str
    how: str  # "exact words", "meaning", "picture", "inside a scan"


DEMO_SUGGESTIONS = (Suggestion("error 0x80070005", "exact words"),
                    Suggestion("paper about denoising diffusion models", "meaning"),
                    Suggestion("dice on a table", "picture"),
                    Suggestion("bayes theorem medical test example", "inside a scan"))
LOCAL_SUGGESTIONS = (Suggestion("screenshot of an error message", "exact words"),
                     Suggestion("notes about a meeting", "meaning"),
                     Suggestion("a photo of a whiteboard", "picture"))


def home(mode: Mode, *, count: int, recent: Sequence[Recent], suggestions: Sequence[Suggestion],
         folders: Sequence[str] = (), samples: int = 0, scope: str = "Both", now: datetime | None = None) -> str:
    tries = "".join(
        f'<button type="button" class="cv-suggest" data-q="{_e(s.query)}">{icon("search")}'
        f'<span class="cv-suggest__q">{_e(s.query)}</span><span class="cv-suggest__how">{_e(s.how)}</span></button>'
        for s in suggestions)
    rows = "".join(
        f'<button type="button" class="cv-row cv-row--compact" data-q="{_e(Path(r.name).stem)}">'
        f'<span class="cv-tile cv-tile--{tile(r.kind, r.name)[0]}">{icon(tile(r.kind, r.name)[1])}</span>'
        f'<span class="cv-row__body"><span class="cv-row__name">{_e(r.name)}</span></span>'
        f'<span class="cv-row__when">{_e(ago(r.indexed_at, now))}</span></button>'
        for r in recent) or '<p class="cv-notice">Nothing indexed yet.</p>'
    if mode.demo:
        extra = (
            '<button type="button" class="cv-drop" data-act="add">' + icon("upload") +
            'Drop your own files here to search them too <span class="cv-drop__hint">· PDF, Office, images, text · '
            "up to 100</span></button>"
            f'<p class="cv-note">{_e(UPLOAD_NOTICE)}</p>'
            f'<p class="cv-note">This demo searches {samples} generated sample files. Text, meaning and pictures are '
            "read by three small models (bge-small, CLIP ViT-B/32, PP-OCRv6) running on this server; nothing is "
            "sent to any outside service. On your own computer, the same app indexes your folders and never leaves "
            "the machine.</p>")
    else:
        items = "".join(f"<li>{_e(f)}</li>" for f in folders) or "<li>No folders yet: use Folders to add one.</li>"
        extra = (f'<section class="cv-col" aria-labelledby="h-folders"><h2 class="cv-section" id="h-folders">'
                 f'Indexed folders</h2><ul class="cv-folders-list">{items}</ul></section>')
    label = f"Search {count} file{'s' if count != 1 else ''} by words, meaning, or what is in a picture"
    return (
        f'<div class="cv-app cv-part" {_render()} data-view="home" data-label="{_e(label)}" data-scope="{_e(scope)}" data-query="">'
        '<main class="cv-home cv-home--below"><div class="cv-cols">'
        f'<section class="cv-col" aria-labelledby="h-try"><h2 class="cv-section" id="h-try">Try a search</h2>{tries}'
        "</section>"
        f'<section class="cv-col" aria-labelledby="h-recent"><h2 class="cv-section" id="h-recent">Recently added</h2>'
        f"{rows}</section></div>{extra}</main></div>"
    )


# ---------------------------------------------------------------- results


@dataclass
class Grouped:
    """Results in display order: Close and Visual matches by their best item's fused rank, Related last."""

    groups: list[tuple[str, list[SearchResult]]]  # ("close" | "visual" | "related", items)

    @property
    def items(self) -> list[SearchResult]:
        return [r for _, rs in self.groups for r in rs]


def group(resp: SearchResponse) -> Grouped:
    close = [(i, r) for i, r in enumerate(resp.results) if any(m != "clip" and v for m, v in r.ranks.items())
             or not any(r.ranks.values())]
    close_ids = {r.file_id for _, r in close}
    visual = [(i, r) for i, r in enumerate(resp.results) if r.file_id not in close_ids]
    ranked = sorted([(items[0][0], name, [r for _, r in items]) for name, items in (("close", close),
                                                                                    ("visual", visual)) if items])
    groups = [(name, items) for _, name, items in ranked]
    if resp.weaker:
        groups.append(("related", list(resp.weaker)))
    return Grouped(groups)


def counts(items: Sequence[SearchResult]) -> dict[str, int]:
    out = {"all": len(items)} | dict.fromkeys(CHIPS, 0)
    for r in items:
        out[r.kind] = out.get(r.kind, 0) + 1
    return out


def details(r: SearchResult, resp: SearchResponse) -> str:
    """The numbers `vault search --debug` prints for this result: each method's rank and RRF share, the total,
    and what the CLIP gate did."""
    rows = []
    for m, label in METHODS.items():
        rank, share = r.ranks.get(m), r.shares.get(m)
        if rank or share:
            rows.append(f"<dt>{_e(label[0].upper() + label[1:])}</dt><dd>rank {rank or '–'} · "
                        f"RRF share {share or 0:.4f}</dd>")
    total = f"{r.rrf_score:.4f}" + (" (among the related matches: meaning without its floor, CLIP without "
                                     "its gate)" if r.weaker else "")
    rows.append(f"<dt>RRF total</dt><dd>{_e(total)}</dd>")
    c = resp.clip
    if c.get("candidates"):
        how = ("z-score over" if c.get("gated") else "fewer than 20 images: top 5 above the cosine floor, of")
        gate = f"{how} {c['candidates']} images; kept {c['kept']} ({c['strong']} strong), weight {c['weight']:.2f}"
        if c.get("visual"):
            gate += "; visual-query rule on"
        rows.append(f"<dt>CLIP gate</dt><dd>{_e(gate)}</dd>")
    if r.clip_status:
        rows.append(f"<dt>This picture</dt><dd>{_e(r.clip_status)}</dd>")
    return f'<details class="cv-details"><summary>Details</summary><dl>{"".join(rows)}</dl></details>'


def preview_template(r: SearchResult, resp: SearchResponse, mode: Mode, place: Place, session: str | None) -> str:
    """The preview for one result, filled in by cortex.js when it's selected (the boxes and the text for Copy text
    come from `/detail/{id}`)."""
    if r.kind in ("photo", "screenshot", "pdf"):
        src = url("preview", r.file_id, session, page=r.page if r.kind == "pdf" else None)
    elif r.thumb_url:
        src = url("thumb", r.file_id, session, v=Path(r.thumb_url).stem[:12])
    else:
        src = ""
    media = (f'<div class="cv-preview__media"><div class="cv-frame"><img src="{_e(src)}" alt="Preview of '
             f'{_e(r.filename)}" loading="lazy"></div></div>' if src else
             f'<div class="cv-preview__media"><div class="cv-preview__ph">{icon("image")}No preview</div></div>')
    facts = ""
    if fb := found_by(r):
        facts += f"<dt>Found by</dt><dd>{_e(fb)}</dd>"
    facts += f"<dt>Text read by</dt><dd>{_e(text_read_by(r, mode))}</dd>"
    if size := size_text(r):
        facts += f'<dt>Size</dt><dd class="cv-num">{_e(size)}</dd>'
    return (f'<template data-preview="{r.file_id}">{media}<div class="cv-preview__head">'
            f'<h2 class="cv-preview__title">{_e(r.filename)}</h2>'
            f'<span class="cv-preview__meta">{_e(preview_meta(r, place))}</span></div>'
            f'<dl class="cv-facts">{facts}</dl>{details(r, resp) if r.ranks else ""}</template>')


def _option_attrs(r: SearchResult, query: str, mode: Mode, session: str | None, hidden: bool, rank: int) -> str:
    opened = url("file", r.file_id, session) if mode.demo else ""
    attrs = {
        "role": "option", "id": f"cv-r-{r.file_id}", "tabindex": "-1", "aria-selected": "false",
        "data-id": str(r.file_id), "data-rank": str(rank), "data-kind": r.kind, "data-title": r.filename, "data-open": opened,
        "data-detail": url("detail", r.file_id, session, chunk=r.chunk_id, page=r.page, q=query),
        "data-link": result_link(query, r.file_id),
    }
    out = " ".join(f'{k}="{_e(v)}"' for k, v in attrs.items())
    return out + (" data-more hidden" if hidden else "")


def row(r: SearchResult, query: str, mode: Mode, session: str | None, hidden: bool = False, rank: int = 0) -> str:
    tint, ico = tile(r.kind, r.filename)
    snip = f'<span class="cv-row__snip">{r.snippet_html}</span>' if r.snippet_html else ""
    return (f'<div class="cv-row" {_option_attrs(r, query, mode, session, hidden, rank)}>'
            f'<span class="cv-tile cv-tile--{tint}">{icon(ico)}</span><span class="cv-row__body">'
            f'<span class="cv-row__top"><span class="cv-row__name">{_e(r.filename)}</span>'
            f'<span class="cv-row__meta">{_e(row_meta(r))}</span></span>{snip}</span>'
            '<span class="cv-row__hint">Enter</span></div>')


def thumb(r: SearchResult, query: str, mode: Mode, session: str | None, hidden: bool = False, rank: int = 0) -> str:
    img = (f'<img src="{_e(url("thumb", r.file_id, session, v=Path(r.thumb_url).stem[:12]))}" alt="" loading="lazy">'
           if r.thumb_url else f'<span class="cv-thumb__ph">{icon("image")}</span>')
    return (f'<div class="cv-thumb" {_option_attrs(r, query, mode, session, hidden, rank)}>'
            f'<span class="cv-thumb__img">{img}</span><span class="cv-thumb__name">{_e(r.filename)}</span></div>')


GROUP_TITLES = {"close": "Close matches",
                "visual": 'Visual matches <span>· found by what is in the picture</span>',
                "related": "Related matches"}


def results(resp: SearchResponse, mode: Mode, place: Place, *, query: str, session: str | None = None,
            scope: str = "Both", select: int | None = None, page: int = PAGE) -> str:
    """The results view: chips with counts, the groups, "Show N more", and the preview pane. `query` is the text
    in the box (normalised) these results belong to; Enter only opens a result when the box still holds it."""
    g = group(resp)
    items = g.items
    if not items:
        return empty(resp, mode, query=query, scope=scope)
    secs = sum(resp.timings_ms.values()) / 1000
    meta = f"{len(items)} result{'s' if len(items) != 1 else ''} · {secs:.2f} s"
    n = counts(items)
    chips = [f'<button type="button" class="cv-kind" aria-pressed="true" data-filter="all">All '
             f'<span class="cv-kind__n">{n["all"]}</span></button>']
    for k in CHIPS:
        label = KIND_LABELS[k]
        chips.append(f'<button type="button" class="cv-kind" aria-pressed="false" data-filter="{k}"'
                     f'{" disabled" if not n[k] else ""}>{_e(label)} <span class="cv-kind__n">{n[k]}</span></button>')
    # The top `page` results by fused rank show, each in its group (the weaker matches rank after every fused
    # result); the rest wait behind "Show N more". So a visual match at #2 shows even when 25 close matches
    # come before its group on the page.
    rank = {r.file_id: i for i, r in enumerate([*resp.results, *resp.weaker])}
    groups_html = []
    for name, rs in g.groups:
        body = []
        for r in rs:
            hidden = rank[r.file_id] >= page
            body.append(thumb(r, query, mode, session, hidden, rank[r.file_id]) if name == "visual"
                        else row(r, query, mode, session, hidden, rank[r.file_id]))
        inner = f'<div class="cv-visual">{"".join(body)}</div>' if name == "visual" else "".join(body)
        all_hidden = all(rank[r.file_id] >= page for r in rs)
        groups_html.append(f'<div class="cv-group" role="group" aria-labelledby="cv-g-{name}" data-group="{name}"'
                           f'{" hidden" if all_hidden else ""}><h2 class="cv-section" id="cv-g-{name}">'
                           f"{GROUP_TITLES[name]}</h2>{inner}</div>")
    if not resp.results:  # nothing close: say so, above the related matches (SPEC §8 states, §7.4 step 4)
        groups_html.insert(0, f'<div class="cv-empty cv-empty--top"><strong>No close matches for “{_e(query)}”'
                              "</strong>Try fewer words, or describe what is in the picture instead, like “a dog on a "
                              "beach”. Related matches are below.</div>")
    more = len(items) - page
    more_btn = (f'<button type="button" class="cv-btn cv-more" data-more-btn>Show {more} more</button>'
                if more > 0 else "")
    templates = "".join(preview_template(r, resp, mode, place, session) for r in items)
    open_label = "Download" if mode.demo else "Open"
    return (
        f'<div class="cv-app cv-part cv-fill" {_render()} data-view="results" data-query="{_e(query)}" data-meta="{_e(meta)}" '
        f'data-scope="{_e(scope)}" data-select="{select or ""}" data-page="{page}">'
        f'<div class="cv-kinds-bar"><div class="cv-kinds" role="group" aria-label="File type">{"".join(chips)}</div>'
        '</div><div class="cv-main">'
        f'<div class="cv-results" id="cv-list" role="listbox" aria-label="Results">{"".join(groups_html)}{more_btn}'
        "</div>"
        '<aside class="cv-preview" aria-label="Preview"><div class="cv-preview__bar"><span>Preview</span>'
        '<button type="button" aria-label="Close preview" data-close>✕</button></div>'
        '<div class="cv-preview__body" data-pv></div>'
        f'<div class="cv-actions"><button type="button" data-act="open">{open_label} <small>Enter</small></button>'
        '<button type="button" data-act="copy">Copy text</button>'
        '<button type="button" data-act="link">Copy link to this result</button></div></aside></div>'
        f'<div hidden data-templates>{templates}</div></div>'
    )


def empty(resp: SearchResponse, mode: Mode, *, query: str, scope: str = "Both") -> str:
    secs = sum(resp.timings_ms.values()) / 1000
    filters = ", ".join(KIND_LABELS[k] for k in sorted(resp.parsed.kinds) if k in KIND_LABELS)
    among = f" among {filters}" if filters else (" in your uploads" if scope == "My uploads" else "")
    return (f'<div class="cv-app cv-part cv-fill" {_render()} data-view="results" data-query="{_e(query)}" '
            f'data-meta="0 results · {secs:.2f} s" data-scope="{_e(scope)}">'
            f'<div class="cv-empty"><strong>No matches for “{_e(query)}”{_e(among)}</strong>'
            "Try fewer words, or describe what is in the picture instead, like “a dog on a beach”.</div></div>")


# ---------------------------------------------------------------- uploads


UPLOAD_ICONS = {"indexed": ("ok", "check", "added"), "duplicate": ("skip", "same", "already in your uploads"),
                "rejected": ("bad", "x", None), "error": ("bad", "x", None)}


def upload_outcome(files: Sequence, *, received: int, scope: str = "My uploads") -> str:
    """After an upload: each file as added, already in your uploads, or why it wasn't (`vault.session.FileStatus`)."""
    added = sum(f.status == "indexed" for f in files)
    rows = []
    for i, f in enumerate(files):
        cls, ico, why = UPLOAD_ICONS.get(f.status, ("bad", "x", None))
        reason = why or f.reason or f.status
        rows.append(f'<div class="cv-upl cv-upl--{cls}"{" data-more hidden" if i >= UPLOAD_ROWS else ""}>'
                    f'{icon(ico)}<span class="cv-upl__name">{_e(f.name)}</span>'
                    f'<span class="cv-upl__why">{_e(reason)}</span></div>')
    more = (f'<button type="button" class="cv-btn cv-more" data-more-btn>Show all {len(files)}</button>'
            if len(files) > UPLOAD_ROWS else "")
    return (f'<div class="cv-app cv-part cv-fill" {_render()} data-view="upload" data-query="" data-scope="{_e(scope)}">'
            f'<div class="cv-results"><h2 class="cv-section">{received} file{"s" if received != 1 else ""} received · '
            f'{added} added</h2><p class="cv-notice">{_e(UPLOAD_NOTICE)}</p>{"".join(rows)}{more}</div></div>')


# ---------------------------------------------------------------- local Folders panel


def folders_stats(stats) -> str:
    """The Folders panel's numbers (`vault.library.Stats`)."""
    tiles = "".join(f"<span><b>{stats.counts.get(k, 0)}</b> {_e(KIND_LABELS[k])}</span>" for k in CHIPS)
    last = ""
    if run := stats.last_run:
        fields = [("added", "added"), ("linked", "renamed or copied"), ("updated", "changed"),
                  ("files_deleted", "removed")]
        parts = [f"{run.get(k, 0)} {label}" for k, label in fields if run.get(k)]
        last = (f'<p class="cv-notice">Last run {_e(ago(stats.last_indexed))}: '
                f'{_e(", ".join(parts) or "nothing changed")}.</p>')
    errors = "".join(f'<div class="cv-upl cv-upl--bad">{icon("x")}<span class="cv-upl__name">{_e(e["path"])}</span>'
                     f'<span class="cv-upl__why">{_e(e["error"] or e["status"])}</span></div>' for e in stats.errors)
    folders = "".join(f"<li>{_e(r)}</li>" for r in stats.roots) or "<li>No folders yet.</li>"
    return (f'<div class="cv-app cv-part"><h2 class="cv-section">Folders</h2><ul class="cv-folders-list">{folders}'
            f'</ul><div class="cv-stats">{tiles}</div>{last}'
            + (f'<h2 class="cv-section">Files that couldn\'t be read</h2>{errors}' if errors else "") + "</div>")


def message(text: str, ok: bool = True) -> str:
    return f'<p class="cv-notice{"" if ok else " cv-err"}">{_e(text)}</p>'


def place_for(demo_root: str | None, session_root: Path | None) -> Place:
    """Where the UI says a stored path is: "Sample files / screenshots", "My uploads", or the folder itself."""

    def place(stored: str) -> str:
        p = Path(stored)
        if session_root is not None and p.is_relative_to(session_root):
            return "My uploads"
        if demo_root and not p.is_absolute():
            parts = PurePosixPath(stored).parts
            if parts and parts[0] == demo_root:
                inner = parts[1:-1]
                return "Sample files" + (f" / {'/'.join(inner)}" if inner else "")
        return str(p.parent)

    return place


def nothing_uploaded(query: str) -> str:
    return (f'<div class="cv-app cv-part cv-fill" {_render()} data-view="results" data-query="{_e(query)}" data-meta="0 results" '
            'data-scope="My uploads"><div class="cv-empty"><strong>Nothing uploaded yet</strong>'
            "Add files to search them, or choose Samples or Both.</div></div>")


def needs_rebuild(query: str, reason: str) -> str:
    return (f'<div class="cv-app cv-part cv-fill" {_render()} data-view="results" data-query="{_e(query)}">'
            f'<div class="cv-empty"><strong>The index needs rebuilding</strong>{_e(reason)}</div></div>')
