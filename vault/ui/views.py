"""HTML for the Cortex Vault UI, "The Brass Index" look (SPEC §8, design/v0-brass/), built on the server for gr.HTML.

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

from vault.library import TEXT_SUFFIXES, TYPES, Recent
from vault.search.engine import SearchResponse, SearchResult
from vault.search.parse import KIND_LABELS

PAGE = 20  # items shown before "Show N more" (SPEC §8)
UPLOAD_ROWS = 10  # upload outcome rows shown before "Show all N"
RECENT = 5  # "Recently added" rows, overall and per type

DEMO_BUSY = "The demo is busy, try again in a few minutes"  # vault.session.BUSY
LOGO = "/cv/logo/cortex-vault-icon.svg"

TYPE_NAMES = {"screenshot": "Screenshot", "pdf": "PDF", "text": "Text", "photo": "Photo", "doc": "Document",
              "slides": "Slides"}
TYPE_PLURALS = {"screenshot": "Screenshots", "pdf": "PDFs", "text": "Text files", "photo": "Photos",
                "doc": "Documents", "slides": "Slides"}
METHODS = {"bm25": "exact words", "all_terms": "every word", "dense": "meaning", "clip": "what is in the picture"}
SHORT_METHODS = {"bm25": "exact words", "all_terms": "every word", "dense": "meaning", "clip": "picture"}

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
    ttl_min: int = 60  # demo: uploads are deleted this long after the last search or upload (`session_ttl_s`)
    max_files: int = 200  # demo: files per session, over all its uploads (`Config.upload_max_files`)
    where: str = field(init=False)  # "on this server" | "on this computer"

    def __post_init__(self) -> None:
        object.__setattr__(self, "where", "on this server" if self.demo else "on this computer")

    @property
    def index_name(self) -> str:
        return "Demo index" if self.demo else "Local index"

    @property
    def status(self) -> str:
        """The header's status line when nothing is being read."""
        return "Demo server · no cloud services" if self.demo else "Local only · nothing leaves this device"

    @property
    def deleted(self) -> str:
        return f"deleted {self.ttl_min} minute{'s' if self.ttl_min != 1 else ''} after your last search or upload"

    @property
    def footer(self) -> str:
        return f"Demo server · your uploads are {self.deleted}" if self.demo else "Nothing leaves this device"

    @property
    def private_note(self) -> str:
        if self.demo:
            return f"Files are read on this server by three small models. Your uploads are {self.deleted}."
        return "Indexing runs on this computer. Nothing leaves this device."

    @property
    def upload_notice(self) -> str:
        return (f"Uploads are processed on this server, kept only for your session, and {self.deleted}. "
                "For private use, run it on your own computer (see README).")


@dataclass(frozen=True)
class Archive:
    """The sidebar's facts (SPEC §8): files per type in what this visitor can search, and the scope."""

    counts: dict[str, int] = field(default_factory=dict)
    scope: str = "Both"
    uploads_full: bool = False  # demo: no new upload session can open now

    @property
    def total(self) -> int:
        return sum(self.counts.values())


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


def _date(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def short_date(iso: str | None) -> str:
    """"12 Oct 2026": the date on a result row."""
    dt = _date(iso)
    return f"{dt.day} {dt:%b %Y}" if dt else ""


def long_date(iso: str | None) -> str:
    dt = _date(iso)
    if dt is None:
        return ""
    return f"{dt.day} {dt:%b %Y}" + (f", {dt:%H:%M}" if (dt.hour, dt.minute) != (0, 0) else "")


def ago(iso: str | None, now: datetime | None = None) -> str:
    """"2 min ago", "3 h ago", "today", or a date. A time ahead of now (an index built in another time zone)
    reads as today."""
    dt = _date(iso)
    if dt is None:
        return ""
    now = now or datetime.now()
    secs = (now - dt).total_seconds()
    if dt.date() == now.date() or 0 > secs > -86400:
        if 0 <= secs < 3600:
            return "just now" if secs < 60 else f"{int(secs // 60)} min ago"
        return f"{int(secs // 3600)} h ago" if 0 <= secs < 6 * 3600 else "today"
    return f"{dt.day} {dt:%b}" + ("" if dt.year == now.year else f" {dt.year}")


def type_of(kind: str, name: str) -> str:
    """The Archive's type: the kind, with plain-text docs as "text"."""
    if kind == "doc" and Path(name).suffix.lower() in TEXT_SUFFIXES:
        return "text"
    return kind if kind in TYPE_NAMES else "doc"


def picture_only(r: SearchResult) -> bool:
    """Only CLIP found it: no text method has a rank."""
    return bool(r.ranks.get("clip")) and not any(v for m, v in r.ranks.items() if m != "clip")


def found_by_rows(r: SearchResult) -> list[tuple[str, int]]:
    """Each method that returned the result, with its real rank (for a weaker match, in the method's full list)."""
    return [(m, rank) for m, rank in r.ranks.items() if rank and m in METHODS]


def text_read_by(r: SearchResult, mode: Mode) -> str:
    ocr = f"OCR, {mode.where}"
    by_source = {"ocr": ocr, "pdf_text": "The PDF's own text layer", "slide_text": "Slide text",
                 "doc_text": "Plain text" if type_of(r.kind, r.filename) == "text" else "Word document text"}
    if r.chunk_source in by_source:
        return by_source[r.chunk_source]
    for source in ("ocr", "pdf_text", "slide_text", "doc_text"):
        if source in r.text_sources:
            return by_source[source]
    return "No text found"


def read_short(r: SearchResult) -> str:
    """How the text was read, as the row's meta line says it."""
    full = text_read_by(r, Mode(demo=True))
    if full.startswith("OCR"):
        return "OCR"
    return {"The PDF's own text layer": "PDF text", "Slide text": "slide text", "Plain text": "plain text",
            "Word document text": "document text"}.get(full, "no text")


def size_text(r: SearchResult) -> str:
    parts = []
    if r.kind in ("photo", "screenshot") and r.width and r.height:
        parts.append(f"{r.width} × {r.height}")
    elif r.kind in ("pdf", "slides") and r.pages:
        parts.append(f"{r.pages} {'slide' if r.kind == 'slides' else 'page'}{'s' if r.pages != 1 else ''}")
    if r.size_bytes is not None:
        parts.append(human_bytes(r.size_bytes))
    return " · ".join(parts)


def where_text(r: SearchResult) -> str:
    """"page 4 of 12" or "slide 7": where in the file the match is."""
    if r.page is None:
        return ""
    unit = "slide" if r.kind == "slides" else "page"
    return f"{unit} {r.page}" + (f" of {r.pages}" if r.pages else "")


def row_meta(r: SearchResult) -> list[str]:
    """The row's meta line before "Found by": where, how the text was read, size."""
    where = where_text(r)
    size = human_bytes(r.size_bytes) if where else size_text(r)
    return [p for p in (where, read_short(r), size) if p]


def detail_meta(r: SearchResult, place: Place) -> str:
    parts = [long_date(r.effective_date), where_text(r), size_text(r)]
    if r.paths:
        parts.append(place(r.paths[0]))
    return " · ".join(p for p in parts if p)


def count_words(matches: int, weaker: int, label: str = "") -> str:
    """The count line after its number: "matches · 36 weaker", "match in Photos". Mirrors `Cortex.countWords`."""
    words = "match" if matches == 1 else "matches"
    if label:
        words += f" in {label}"
    return words + (f" · {weaker} weaker" if weaker else "")


# ---------------------------------------------------------------- URLs (ids only, never paths)


def url(route: str, file_id: int, session: str | None = None, **params: object) -> str:
    query = {k: v for k, v in params.items() if v is not None and v != ""}
    if session:
        query["s"] = session
    return f"/{route}/{int(file_id)}" + (f"?{urlencode(query)}" if query else "")


def result_link(query: str, file_id: int) -> str:
    """Copy link to this result: the search and the file id, never a path."""
    return "?" + urlencode({"q": query, "r": int(file_id)})


def thumb_url(r: SearchResult, session: str | None) -> str:
    version = Path(r.thumb_url).stem[:12] if r.thumb_url else None
    return url("thumb", r.file_id, session, v=version)


# ---------------------------------------------------------------- icons


_FILE = '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'
ICONS = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "arrow-down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2'
           'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "shield": '<path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
    "zap": '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    "upload": '<path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
    "folder": '<path d="M3 6a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',
    "archive": _FILE + '<path d="M10 8h1M10 11h1M10 14h1M11 17h1"/>',
    "screenshot": _FILE + '<rect x="8.5" y="11.5" width="7" height="5.5" rx="1"/><circle cx="10.6" cy="13.6" r=".6"/>',
    "photo": _FILE + '<circle cx="10" cy="12" r="1.3"/><path d="M8 18l3-3 2 2 1.5-1.5L17 18"/>',
    "pdf": _FILE + '<path d="M9 12h6M12 12v6"/>',
    "text": _FILE + '<path d="M9 12h6M9 15h6M9 18h4"/>',
    "doc": _FILE + '<path d="M9 12h6M9 15h6M9 18h6"/>',
    "slides": _FILE + '<rect x="8.5" y="11" width="7" height="5" rx="1"/><path d="M12 16v2"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 17l-5-5-9 8"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "same": '<path d="M5 9h14M5 15h14"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h9"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 '
            '5.7 5.7l1-1"/>',
    "download": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    "open": '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "keyboard": '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
}
# The dial's face (SPEC §8): brass ring, gradient face, dashed ring of ticks and the notch. The hub is our logo,
# an <img> over it, so it stays upright while the face turns.
DIAL = ('<radialGradient id="cv-dial-g" cx="35%" cy="30%" r="75%"><stop offset="0" stop-color="#ebc78f"/>'
        '<stop offset="1" stop-color="#9a6336"/></radialGradient>'
        '<symbol id="cv-dial" viewBox="-14 -14 188 188">'
        '<circle cx="80" cy="80" r="76" fill="#aa713d"/><circle cx="80" cy="80" r="67" fill="url(#cv-dial-g)"/>'
        '<circle cx="80" cy="80" r="65.5" fill="none" stroke="#6e4325" stroke-width="3"/>'
        '<circle cx="80" cy="80" r="56" fill="none" stroke="#442612" stroke-opacity=".42" stroke-width="2" '
        'stroke-dasharray="5 4.6"/>'
        '<rect x="78.5" y="-13" width="3" height="19" rx="1.5" fill="#f4d09a"/></symbol>')


def icon(name: str, cls: str = "cv-ico") -> str:
    return f'<svg class="{cls}" aria-hidden="true"><use href="#i-{name}"/></svg>'


def sprite() -> str:
    symbols = "".join(f'<symbol id="i-{k}" viewBox="0 0 24 24">{v}</symbol>' for k, v in ICONS.items())
    return f'<svg width="0" height="0" style="position:absolute" aria-hidden="true">{symbols}{DIAL}</svg>'


def glyph(kind_or_type: str, cls: str = "") -> str:
    t = kind_or_type if kind_or_type in TYPE_NAMES else "doc"
    return f'<span class="cv-glyph cv-glyph--{t}{" " + cls if cls else ""}">{icon(t)}</span>'


def dial(compact: bool = False) -> str:
    return (f'<span class="cv-dial{" cv-dial--compact" if compact else ""}" aria-hidden="true">'
            '<span class="cv-dial__spin"><svg class="cv-dial__face"><use href="#cv-dial"/></svg></span>'
            f'<img class="cv-dial__hub" src="{LOGO}" alt=""></span>')


# ---------------------------------------------------------------- page chrome


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


def top(mode: Mode, suggestions: Sequence[Suggestion] = ()) -> str:
    """Header, hero, search box and the recent / example searches: rendered once; cortex.js keeps them in step
    with the other parts (the status, the query, the recent searches it keeps in this browser)."""
    upload = ('<input type="file" class="cv-sr" data-files multiple tabindex="-1" aria-hidden="true">'
              if mode.demo else "")
    examples = "".join(
        f'<button type="button" class="cv-chip" data-q="{_e(s.query)}" title="Found by {_e(s.how)}">{_e(s.query)}'
        "</button>" for s in suggestions)
    return (
        f'<div class="cv-app cv-part">{sprite()}'
        '<header class="cv-header"><div class="cv-wrap cv-header__in">'
        f'<a class="cv-brand" href="./" data-home><img class="cv-brand__logo" src="{LOGO}" alt="" width="34" '
        'height="34"><span><span class="cv-brand__name">Cortex <span>Vault</span></span>'
        f'<span class="cv-brand__sub">Private {"demo" if mode.demo else "local"} index</span></span></a>'
        '<div class="cv-header__end"><span class="cv-status" role="status" data-status><span class="cv-dot"></span>'
        f'<span data-status-text>{_e(mode.status)}</span></span>{dial(compact=True)}'
        '<button type="button" class="cv-iconbtn" data-act="theme" aria-label="Dark theme" aria-pressed="false">'
        f'{icon("moon", "cv-ico cv-ico--moon")}{icon("sun", "cv-ico cv-ico--sun")}</button></div></div></header>'
        f"{upload_bar() if mode.demo else ''}"
        '<div class="cv-wrap cv-top">'
        '<section class="cv-hero" data-hero><div class="cv-hero__copy">'
        f'<p class="cv-eyebrow">{icon("shield")}Your files, remembered</p>'
        '<h1>A vault for <em>everything</em> you saved.</h1></div>'
        '<div class="cv-hero__object"><div class="cv-dial-label">'
        f'<span>{_e(mode.index_name)}</span><span class="cv-dial-label__dot">•</span><span data-dial-state>Ready</span>'
        f'</div>{dial()}<div class="cv-hero__caption">{icon("zap")}Three small models. One quiet vault.</div></div>'
        "</section>"
        '<div class="cv-search">' + icon("search", "cv-ico cv-search__ico") +
        '<input id="cv-q" type="search" placeholder="Search your vault…" autocomplete="off" spellcheck="false" '
        'role="combobox" aria-expanded="false" aria-controls="cv-list" aria-autocomplete="list" '
        'aria-label="Search your vault by words, meaning, or what is in a picture">'
        f'<button type="button" class="cv-search__clear" data-act="clear" aria-label="Clear search" hidden>'
        f'{icon("x")}</button><kbd class="cv-kbd" data-kbd>Ctrl K</kbd>'
        f'<button type="button" class="cv-search__go" data-act="go" aria-label="Search now">{icon("arrow-down")}'
        "</button></div>"
        '<div class="cv-queries" data-queries><span class="cv-queries__label" data-queries-label>Try a search</span>'
        f'<span class="cv-queries__list" data-examples>{examples}</span>'
        '<span class="cv-queries__list" data-recent hidden></span>'
        '<button type="button" class="cv-queries__clear" data-act="forget-all" hidden>Clear</button></div>'
        f"{upload}</div></div>"
    )


SOURCE_URL = "https://github.com/swarup0077/cortex-vault"  # offered to the demo's users (AGPL-3.0 section 13)


def footer(mode: Mode) -> str:
    """The mode line, the index line, a link to the source code (demo only: a plain link, nothing loads until it's
    clicked), and the keyboard hints."""
    source = (f'<a class="cv-footer__src" href="{SOURCE_URL}" target="_blank" rel="noopener noreferrer">Source '
              "code (AGPL-3.0)</a>") if mode.demo else ""
    return (f'<footer class="cv-app cv-part cv-footer"><div class="cv-wrap cv-footer__in">'
            f'<span class="cv-footer__mode"><span class="cv-dot"></span>{_e(mode.footer)}</span>{source}'
            '<span data-index-line></span><span class="cv-keys">' + icon("keyboard") +
            '<span><b data-kbd>Ctrl K</b> search</span><span><b>↑↓</b> move</span><span><b>↵</b> open</span>'
            "<span><b>esc</b> clear</span></span></div></footer>")


def upload_bar() -> str:
    """The upload progress bar (SPEC §8): a slim strip under the header, in the page flow, so search, the type
    filter and the detail view stay usable. cortex.js fills it from its own transfer counts (stage 1) and from the
    status part (listing and reading); the report rows come from the status part, rendered here on the server."""
    return ('<div class="cv-bar" data-bar hidden><div class="cv-wrap cv-bar__in">'
            '<span class="cv-bar__text" data-bar-text></span>'
            '<span class="cv-bar__track" role="progressbar" aria-label="Upload progress" data-bar-track>'
            '<i data-bar-fill></i></span><span class="cv-bar__actions">'
            '<button type="button" class="cv-bar__btn" data-act="update-results" hidden>Update results</button>'
            '<button type="button" class="cv-bar__link" data-act="report" aria-expanded="false" hidden></button>'
            f'<button type="button" class="cv-iconbtn cv-bar__close" data-act="dismiss" aria-label="Hide" hidden>'
            f'{icon("x")}</button></span></div>'
            '<div class="cv-wrap cv-bar__report" data-bar-report hidden></div></div>'
            '<span class="cv-sr" role="status" aria-live="polite" data-bar-say></span>')


def status(text: str, *, index: str = "", dial_state: str = "Ready", stage: str = "idle", done: int = 0,
           total: int = 0, version: int = 0, uploads_full: bool = False, upload_id: str = "", report: str = "",
           summary: str = "", message: str = "", new_session: bool = False) -> str:
    """What cortex.js copies into place: the header status, the dial's state, the footer's index line, and the
    upload bar's server stages. `stage`: "list" or "read" while an upload is indexed (`done` of `total`, real
    counts), "done" after it, else "idle". `version`: the index's `Library.version()`, so results older than the
    index offer "Update results". `upload_id` names one upload; `report` is its per-file outcome (server-rendered
    rows) and `summary` the line that opens it; `message` says why nothing was kept. `uploads_full`: no new upload
    session can open, so Add files and the drop zone say so instead of uploading."""
    busy = stage in ("list", "read")
    return (f'<div class="cv-app cv-part" data-status-src data-busy="{str(busy).lower()}" data-text="{_e(text)}" '
            f'data-index="{_e(index)}" data-dial="{_e(dial_state)}" data-stage="{_e(stage)}" data-done="{int(done)}" '
            f'data-total="{int(total)}" data-version="{int(version)}" data-upload="{_e(upload_id)}" '
            f'data-summary="{_e(summary)}" data-message="{_e(message)}" data-new-session="{str(new_session).lower()}" '
            f'data-uploads="{"full" if uploads_full else "open"}" data-busy-text="{_e(DEMO_BUSY)}">'
            f'{f"<template data-report>{report}</template>" if report else ""}</div>')


# ---------------------------------------------------------------- The Archive (sidebar)


def rail(mode: Mode, archive: Archive) -> str:
    scope = ""
    if mode.demo:
        buttons = "".join(f'<button type="button" data-scope-btn="{s}" aria-pressed="{str(s == archive.scope).lower()}">'
                          f"{s}</button>" for s in ("Samples", "My uploads", "Both"))
        scope = (f'<div class="cv-rail__scope"><div class="cv-rail__label" id="cv-scope-l">Search in</div>'
                 f'<div class="cv-seg" role="group" aria-labelledby="cv-scope-l">{buttons}</div></div>')
    filters = [f'<button type="button" class="cv-filter" data-filter="all" aria-pressed="true">{glyph("archive")}'
               f'<span class="cv-filter__name">All files</span><span class="cv-filter__n">{archive.total}</span></button>']
    for t in TYPES:
        n = archive.counts.get(t, 0)
        filters.append(f'<button type="button" class="cv-filter" data-filter="{t}" aria-pressed="false"'
                       f'{" disabled" if not n else ""}>{glyph(t)}<span class="cv-filter__name">{TYPE_NAMES[t]}</span>'
                       f'<span class="cv-filter__n">{n}</span></button>')
    if not mode.demo:
        action = f'<button type="button" class="cv-add" data-act="folders">{icon("folder")}Folders</button>'
    elif archive.uploads_full:
        action = f'<div class="cv-add cv-add--busy" role="status">{_e(DEMO_BUSY)}</div>'
    else:
        action = f'<button type="button" class="cv-add" data-act="add">{icon("upload")}Add files</button>'
    return (
        '<aside class="cv-rail" aria-label="The Archive">'
        '<div class="cv-rail__head"><h2>The Archive</h2>'
        f'<span class="cv-rail__count">{archive.total} file{"s" if archive.total != 1 else ""}</span></div>{scope}'
        '<div class="cv-rail__types"><div class="cv-rail__label" id="cv-types-l">Filter by type</div>'
        f'<div class="cv-filters" role="group" aria-labelledby="cv-types-l">{"".join(filters)}</div></div>'
        f'<div class="cv-rail__rest"><div class="cv-rail__note"><div class="cv-rail__note-title">{icon("shield")}'
        f'Private by design</div><p>{_e(mode.private_note)}</p></div>{action}</div></aside>'
    )


def _page(view: str, mode: Mode, archive: Archive, content: str, *, query: str = "", **data: object) -> str:
    """One main-part view: The Archive beside `content`. `data` becomes data-* attributes on the root."""
    attrs = "".join(f' data-{k.replace("_", "-")}="{_e(v)}"' for k, v in data.items() if v is not None)
    return (f'<div class="cv-app cv-part" {_render()} data-view="{view}" data-query="{_e(query)}" '
            f'data-scope="{_e(archive.scope)}"{attrs}><div class="cv-wrap cv-layout">{rail(mode, archive)}'
            f'<div class="cv-content">{content}</div></div></div>')


# ---------------------------------------------------------------- home


def home(mode: Mode, archive: Archive, *, recent: Sequence[Recent], folders: Sequence[str] = (), samples: int = 0,
         now: datetime | None = None) -> str:
    """Home below the search box: Recently added (`recent` holds the newest overall and per type, newest first;
    cortex.js shows the ones the type filter asks for), then the drop zone and notes, or the indexed folders."""
    rows = []
    for i, r in enumerate(recent):
        t = type_of(r.kind, r.name)
        rows.append(f'<button type="button" class="cv-recent" data-q="{_e(Path(r.name).stem)}" data-type="{t}" '
                    f'data-top="{int(i < RECENT)}"{" hidden" if i >= RECENT else ""}>{glyph(t)}'
                    f'<span class="cv-recent__name">{_e(r.name)}</span>'
                    f'<span class="cv-recent__when">{_e(ago(r.indexed_at, now))}</span></button>')
    listing = "".join(rows) or '<p class="cv-note">Nothing indexed yet.</p>'
    none = '<p class="cv-note" data-recent-none hidden>None of this type yet.</p>' if rows else ""
    if mode.demo:
        drop = ('<button type="button" class="cv-drop" data-act="add">' + icon("upload") +
                'Drop your own files here to search them too <span class="cv-drop__sep">·</span>'
                f'<span class="cv-drop__hint">PDF, Office, images, text · up to {mode.max_files}</span></button>')
        if archive.uploads_full:
            drop = (f'<div class="cv-drop cv-drop--busy" role="status">{_e(DEMO_BUSY)} '
                    '<span class="cv-drop__hint">· searching the sample files works as usual</span></div>')
        extra = (
            drop +
            f'<p class="cv-note">{_e(mode.upload_notice)}</p>'
            f'<p class="cv-note">This demo searches {samples} sample file{"s" if samples != 1 else ""}. '
            "Text, meaning and pictures are read by three small models (bge-small, CLIP ViT-B/32, PP-OCRv6) running on this server; nothing is "
            "sent to any outside service. On your own computer, the same app indexes your folders and never leaves "
            "the machine.</p>")
    else:
        items = "".join(f"<li>{_e(f)}</li>" for f in folders) or "<li>No folders yet: use Folders to add one.</li>"
        extra = (f'<section aria-labelledby="h-folders"><h2 class="cv-heading" id="h-folders">Indexed folders</h2>'
                 f'<ul class="cv-folders-list">{items}</ul></section>')
    content = (f'<section class="cv-home" aria-labelledby="h-recent"><h2 class="cv-heading" id="h-recent">'
               f'Recently added</h2><div class="cv-recents">{listing}</div>{none}</section>{extra}')
    return _page("home", mode, archive, content)


# ---------------------------------------------------------------- results


def counts_by_type(items: Sequence[SearchResult]) -> dict[str, int]:
    out = dict.fromkeys(TYPES, 0)
    for r in items:
        out[type_of(r.kind, r.filename)] += 1
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
    total = f"{r.rrf_score:.4f}" + (f" (among the weaker matches: {r.below})" if r.weaker else "")
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


def preview_template(r: SearchResult, resp: SearchResponse, mode: Mode, place: Place, session: str | None,
                     number: int) -> str:
    """The detail view for one result, filled in by cortex.js when it's selected (the boxes and the text for
    Copy text come from `/detail/{id}`)."""
    if r.kind in ("photo", "screenshot", "pdf"):
        src = url("preview", r.file_id, session, page=r.page if r.kind == "pdf" else None)
    elif r.thumb_url:
        src = thumb_url(r, session)
    else:
        src = ""
    t = type_of(r.kind, r.filename)
    media = (f'<div class="cv-detail__media"><div class="cv-frame"><img src="{_e(src)}" alt="Preview of '
             f'{_e(r.filename)}"></div></div>' if src else
             f'<div class="cv-detail__media"><div class="cv-detail__ph">{icon("image")}No preview</div></div>')
    found = "".join(f'<div class="cv-detail__method"><span>{_e(METHODS[m])}</span><b>rank {rank}</b>{icon("check")}'
                    "</div>" for m, rank in found_by_rows(r))
    found_sec = (f'<section class="cv-detail__sec"><h3>Found by</h3>{found}{details(r, resp) if r.ranks else ""}'
                 "</section>" if found else "")
    text_sec = (f'<section class="cv-detail__sec"><h3>Extracted text</h3><p class="cv-detail__text">{r.snippet_html}'
                "</p></section>" if r.snippet_html else "")
    open_label, open_icon = ("Download", "download") if mode.demo else ("Open", "open")
    return (f'<template data-preview="{r.file_id}"><div class="cv-detail__kicker">Result {number:02d} / '
            f'{_e(TYPE_NAMES[t])}</div>{media}{glyph(t)}<h2 class="cv-detail__title">{_e(r.filename)}</h2>'
            f'<p class="cv-detail__meta">{_e(detail_meta(r, place))}</p>'
            f'<div class="cv-detail__actions"><button type="button" data-act="open">{icon(open_icon)}{open_label}'
            f'<small>Enter</small></button><button type="button" data-act="copy" disabled>{icon("copy")}Copy text'
            f'</button><button type="button" data-act="link">{icon("link")}Copy link</button></div>'
            f'{found_sec}<section class="cv-detail__sec"><h3>Text read by</h3><p>{_e(text_read_by(r, mode))}</p>'
            f"</section>{text_sec}</template>")


def _option_attrs(r: SearchResult, query: str, mode: Mode, session: str | None, hidden: bool, rank: int) -> str:
    opened = url("file", r.file_id, session) if mode.demo else ""
    pic = picture_only(r)  # no boxes and no passage for a match only CLIP found (SPEC §8)
    attrs = {
        "role": "option", "id": f"cv-r-{r.file_id}", "tabindex": "-1", "aria-selected": "false",
        "data-id": str(r.file_id), "data-rank": str(rank), "data-type": type_of(r.kind, r.filename),
        "data-title": r.filename, "data-open": opened,
        "data-detail": url("detail", r.file_id, session, chunk=None if pic else r.chunk_id, page=r.page,
                           q=None if pic else query),
        "data-link": result_link(query, r.file_id),
    }
    out = " ".join(f'{k}="{_e(v)}"' for k, v in attrs.items())
    return out + (" data-weaker" if r.weaker else "") + (" data-more hidden" if hidden else "")


def row(r: SearchResult, query: str, mode: Mode, session: str | None, hidden: bool = False, rank: int = 0) -> str:
    t = type_of(r.kind, r.filename)
    snip = f'<span class="cv-card__snip">{r.snippet_html}</span>' if r.snippet_html else ""
    strip = ""
    if r.kind in ("photo", "screenshot"):
        img = (f'<img src="{_e(thumb_url(r, session))}" alt="" loading="lazy">' if r.thumb_url
               else icon("image"))
        strip = (f'<span class="cv-strip"><span class="cv-strip__img">{img}</span><span class="cv-strip__cap">'
                 f'{"Picture match" if picture_only(r) else "Matched text"}</span></span>')
    meta = "".join(f"<span>{_e(p)}</span>" for p in row_meta(r))
    methods = "".join(f'<span class="cv-method cv-method--{m}"><i></i>{_e(SHORT_METHODS[m])} <b>#{n}</b></span>'
                      for m, n in found_by_rows(r))
    found = f'<span class="cv-card__found">Found by</span>{methods}' if methods else ""
    return (f'<div class="cv-card" {_option_attrs(r, query, mode, session, hidden, rank)}>'
            f'<span class="cv-card__rank">{rank + 1:02d}</span>{glyph(t)}<span class="cv-card__body">'
            f'<span class="cv-card__title"><span class="cv-card__name">{_e(r.filename)}</span>'
            f'<span class="cv-card__date">{_e(short_date(r.effective_date))}</span></span>{snip}{strip}'
            f'<span class="cv-card__meta">{meta}{found}</span></span><span class="cv-card__hint">Enter</span></div>')


def results(resp: SearchResponse, mode: Mode, place: Place, archive: Archive | None = None, *, query: str,
            session: str | None = None, select: int | None = None, page: int = PAGE, version: int = 0) -> str:
    """The results view: the count line, one numbered list in fused order with the weaker matches after a
    divider, "Show N more", and the detail view. `query` is the text in the box (normalised) these results
    belong to; Enter only opens a result when the box still holds it. `version`: the index's version they were
    found in (`Library.version()`); a newer index makes the upload bar offer "Update results"."""
    archive = archive or Archive()
    items = [*resp.results, *resp.weaker]
    if not items:
        return empty(resp, mode, archive, query=query)
    secs = sum(resp.timings_ms.values()) / 1000
    n, w = len(resp.results), len(resp.weaker)
    count = (f'<div class="cv-count" data-count><span class="cv-count__n" data-count-n>{n}</span> '
             f'<span data-count-words>{_e(count_words(n, w))}</span>'
             '<button type="button" class="cv-count__clear" data-clear-filter aria-label="Clear the type filter" '
             f'hidden>{icon("x")}</button><span class="cv-count__time">in {secs:.2f} s</span></div>')
    matches = "".join(row(r, query, mode, session, i >= page, i) for i, r in enumerate(resp.results))
    weaker = "".join(row(r, query, mode, session, n + i >= page, n + i) for i, r in enumerate(resp.weaker))
    groups = [f'<div role="group" aria-label="Matches" data-group="matches">{matches}</div>' if matches else ""]
    if weaker:
        groups.append(f'<div role="group" aria-labelledby="cv-weaker-h" data-group="weaker"'
                      f'{" hidden" if n >= page else ""}><div class="cv-divider" id="cv-weaker-h">'
                      f"<span>Weaker matches</span></div>{weaker}</div>")
    top_note = ""
    if not resp.results:  # nothing close: say so, above the weaker matches (SPEC §8 states, §7.4 step 4)
        top_note = (f'<div class="cv-empty cv-empty--top"><strong>No close matches for “{_e(query)}”</strong>'
                    "Try fewer words, or describe what is in the picture instead, like “a dog on a beach”. "
                    "Weaker matches are below.</div>")
    more = len(items) - page
    more_btn = (f'<button type="button" class="cv-more" data-more-btn>Show {more} more</button>' if more > 0 else "")
    filter_empty = ('<div class="cv-empty" data-filter-empty hidden><strong data-filter-empty-text></strong>'
                    '<button type="button" class="cv-more" data-clear-filter>Show all types</button></div>')
    templates = "".join(preview_template(r, resp, mode, place, session, i + 1) for i, r in enumerate(items))
    content = (
        f'<div class="cv-toolbar">{count}</div><div class="cv-split">'
        f'<div class="cv-results">{top_note}<div id="cv-list" role="listbox" aria-label="Results">{"".join(groups)}'
        f"</div>{filter_empty}{more_btn}</div>"
        '<div class="cv-backdrop" data-close hidden></div>'
        '<aside class="cv-detail" aria-label="Selected result"><div class="cv-detail__bar"><span>Preview</span>'
        f'<button type="button" class="cv-iconbtn" aria-label="Close" data-close>{icon("x")}</button></div>'
        '<div class="cv-detail__body" data-pv></div></aside></div>'
        f'<div hidden data-templates>{templates}</div>'
    )
    return _page("results", mode, archive, content, query=query, select=select or "", page=page,
                 matches=n, weaker=w, version=version)


def empty(resp: SearchResponse, mode: Mode, archive: Archive | None = None, *, query: str) -> str:
    archive = archive or Archive()
    secs = sum(resp.timings_ms.values()) / 1000
    filters = ", ".join(KIND_LABELS[k] for k in sorted(resp.parsed.kinds) if k in KIND_LABELS)
    among = f" among {filters}" if filters else (" in your uploads" if archive.scope == "My uploads" else "")
    content = (f'<div class="cv-toolbar"><div class="cv-count"><span class="cv-count__n">0</span> matches'
               f'<span class="cv-count__time">in {secs:.2f} s</span></div></div>'
               f'<div class="cv-empty"><strong>No matches for “{_e(query)}”{_e(among)}</strong>'
               "Try fewer words, or describe what is in the picture instead, like “a dog on a beach”.</div>")
    return _page("results", mode, archive, content, query=query)


def _message(view: str, mode: Mode, archive: Archive, title: str, text: str, query: str = "") -> str:
    return _page(view, mode, archive, f'<div class="cv-empty"><strong>{_e(title)}</strong>{_e(text)}</div>',
                 query=query)


def nothing_uploaded(mode: Mode, archive: Archive, query: str) -> str:
    return _message("results", mode, archive, "Nothing uploaded yet",
                    "Add files to search them, or choose Samples or Both.", query)


def needs_rebuild(mode: Mode, archive: Archive, query: str, reason: str) -> str:
    return _message("results", mode, archive, "The index needs rebuilding", reason, query)


# ---------------------------------------------------------------- uploads


UPLOAD_ICONS = {"indexed": ("ok", "check", "added"), "duplicate": ("skip", "same", "already in your uploads"),
                "rejected": ("bad", "x", None), "error": ("bad", "x", None)}


UPLOAD_KEPT_NONE = "Your files weren't kept. Searching the sample files works as usual."
UPLOAD_FAILED = "The upload couldn't be read. Please try again."


def upload_summary(files: Sequence) -> str:
    """The upload bar's line for one upload's outcome: "3 added · 1 already in your uploads · 1 not added"."""
    added = sum(f.status == "indexed" for f in files)
    same = sum(f.status == "duplicate" for f in files)
    parts = [f"{added} added"] + ([f"{same} already in your uploads"] if same else [])
    if rest := len(files) - added - same:
        parts.append(f"{rest} not added")
    return " · ".join(parts)


def upload_report(files: Sequence, mode: Mode, *, received: int) -> str:
    """The rows behind the bar's summary: each file as added, already in your uploads, or why it wasn't
    (`vault.session.FileStatus`), under the upload notice. cortex.js copies them into the bar when it's opened."""
    rows = []
    for i, f in enumerate(files):
        cls, ico, why = UPLOAD_ICONS.get(f.status, ("bad", "x", None))
        reason = why or f.reason or f.status
        rows.append(f'<div class="cv-upl cv-upl--{cls}"{" data-more hidden" if i >= UPLOAD_ROWS else ""}>'
                    f'{icon(ico)}<span class="cv-upl__name">{_e(f.name)}</span>'
                    f'<span class="cv-upl__why">{_e(reason)}</span></div>')
    more = (f'<button type="button" class="cv-more" data-more-btn>Show all {len(files)}</button>'
            if len(files) > UPLOAD_ROWS else "")
    return (f'<p class="cv-note">{received} file{"s" if received != 1 else ""} received. {_e(mode.upload_notice)}</p>'
            f'<div class="cv-upls">{"".join(rows)}</div>{more}')


# ---------------------------------------------------------------- local Folders panel


def folders_stats(stats) -> str:
    """The Folders panel's numbers (`vault.library.Stats`)."""
    tiles = "".join(f"<span><b>{stats.counts.get(k, 0)}</b> {_e(label)}</span>" for k, label in KIND_LABELS.items())
    last = ""
    if run := stats.last_run:
        fields = [("added", "added"), ("linked", "renamed or copied"), ("updated", "changed"),
                  ("files_deleted", "removed")]
        parts = [f"{run.get(k, 0)} {label}" for k, label in fields if run.get(k)]
        last = (f'<p class="cv-note">Last run {_e(ago(stats.last_indexed))}: '
                f'{_e(", ".join(parts) or "nothing changed")}.</p>')
    errors = "".join(f'<div class="cv-upl cv-upl--bad">{icon("x")}<span class="cv-upl__name">{_e(e["path"])}</span>'
                     f'<span class="cv-upl__why">{_e(e["error"] or e["status"])}</span></div>' for e in stats.errors)
    folders = "".join(f"<li>{_e(r)}</li>" for r in stats.roots) or "<li>No folders yet.</li>"
    return (f'<div class="cv-app cv-part"><h2 class="cv-heading">Folders</h2><ul class="cv-folders-list">{folders}'
            f'</ul><div class="cv-stats">{tiles}</div>{last}'
            + (f'<h2 class="cv-heading">Files that couldn\'t be read</h2>{errors}' if errors else "") + "</div>")


def message(text: str, ok: bool = True) -> str:
    return f'<p class="cv-note{"" if ok else " cv-err"}">{_e(text)}</p>'


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
