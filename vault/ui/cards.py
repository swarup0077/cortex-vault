"""HTML for the Search tab: result cards, the Visual matches row and the detail panel (SPEC §8).

Pure functions from search results to HTML strings, so they're testable without a server. Every value from the
index is escaped here; snippets arrive escaped already, with only `<mark>` added. Images are referenced by file
id (`/thumb/{id}`, `/preview/{id}`), never by path.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from vault.library import Detail
from vault.search.engine import SearchResponse, SearchResult
from vault.search.parse import KIND_LABELS

KIND_NAMES = {"screenshot": "Screenshot", "photo": "Photo", "pdf": "PDF", "doc": "Doc", "slides": "Slides"}
RANK_NAMES = {"bm25": "BM25", "all_terms": "all terms", "dense": "dense", "clip": "CLIP"}

Display = Callable[[str], str]  # a stored path as the UI shows it (Library.display)


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def nice_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:10]
    return f"{dt.day} {dt:%b %Y}"


def image_url(kind: str, file_id: int, session: str | None = None, **params: object) -> str:
    """`/thumb/{id}` or `/preview/{id}`, with the session id when the file is in a session's index."""
    query = {k: v for k, v in params.items() if v is not None}
    if session:
        query["s"] = session
    return f"/{kind}/{file_id}" + (f"?{urlencode(query)}" if query else "")


def thumb_url(r: SearchResult, session: str | None) -> str | None:
    if not r.thumb_url:
        return None
    return image_url("thumb", r.file_id, session, v=Path(r.thumb_url).stem[:12])  # the sha: a new file, a new URL


def _where(r: SearchResult) -> str:
    if r.page is None:
        return ""
    return f"{'slide' if r.kind == 'slides' else 'page'} {r.page}"


def card(r: SearchResult, rank: int, session: str | None, display: Display, show_scores: bool = False) -> str:
    thumb = thumb_url(r, session)
    img = (f'<img src="{_e(thumb)}" alt="" loading="lazy">' if thumb
           else f'<span class="v-noimg">{_e(KIND_NAMES.get(r.kind, r.kind))}</span>')
    meta = [f'<span class="v-kind v-kind-{_e(r.kind)}">{_e(KIND_NAMES.get(r.kind, r.kind))}</span>']
    if where := _where(r):
        meta.append(f'<span class="v-where">{_e(where)}</span>')
    if date := nice_date(r.effective_date):
        meta.append(f"<span>{_e(date)}</span>")
    if r.copies:
        meta.append(f'<span class="v-copies">+{r.copies} {"copy" if r.copies == 1 else "copies"}</span>')
    for badge in r.badges:
        meta.append(f'<span class="v-badge">{_e(badge)}</span>')
    scores = ""
    if show_scores:
        ranks = " · ".join(f"{RANK_NAMES.get(k, k)} #{v}" for k, v in r.ranks.items() if v)
        scores = f'<div class="v-scores">rrf {r.rrf_score:.4f}{" · " + _e(ranks) if ranks else ""}</div>'
    snippet = f'<p class="v-snippet">{r.snippet_html}</p>' if r.snippet_html else ""
    folder = str(Path(display(r.paths[0])).parent) if r.paths else r.folder
    return (
        f'<button type="button" class="v-card" data-file="{r.file_id}" data-chunk="{r.chunk_id or ""}" '
        f'data-page="{r.page or ""}" aria-label="{_e(r.filename)}">'
        f'<span class="v-thumb v-thumb-{_e(r.kind)}">{img}</span>'
        f'<span class="v-body"><span class="v-title"><span class="v-rank">{rank}</span>{_e(r.filename)}</span>'
        f'<span class="v-meta">{"".join(meta)}</span>{snippet}'
        f'<span class="v-folder">{_e(folder)}</span>'
        f"{scores}</span></button>"
    )


def visual_row(results: list[SearchResult], session: str | None) -> str:
    """CLIP's gated hits as a strip of thumbnails (open item t): a strong visual match stays visible even when
    text files outrank it in the list."""
    if not results:
        return ""
    tiles = "".join(
        f'<button type="button" class="v-tile" data-file="{r.file_id}" data-chunk="{r.chunk_id or ""}" '
        f'data-page="" title="{_e(r.filename)}" aria-label="{_e(r.filename)}">'
        f'<img src="{_e(thumb_url(r, session) or "")}" alt="" loading="lazy"></button>'
        for r in results
    )
    return ('<section class="v-visual" aria-label="Visual matches"><h3>Visual matches '
            '<span>found by what the picture shows</span></h3>'
            f'<div class="v-strip">{tiles}</div></section>')


def results_html(resp: SearchResponse, session: str | None, display: Display, *, page: int,
                 show_scores: bool = False, scope_note: str = "") -> str:
    """The whole result area: summary line, Visual matches row, the first `page` cards, the rest behind
    "Show more", and the weaker matches under their divider."""
    rows = resp.results + resp.weaker
    if not rows:
        filters = ", ".join(KIND_LABELS[k] for k in sorted(resp.parsed.kinds) if k in KIND_LABELS)
        hint = f" among {_e(filters)}" if filters else ""
        return (f'<div class="v-empty"><p class="v-empty-title">No results{hint}.</p>'
                f"<p>Try other words, or remove a filter.</p></div>")
    ms = sum(resp.timings_ms.values())
    kinds = ", ".join(KIND_LABELS[k] for k in sorted(resp.parsed.kinds) if k in KIND_LABELS)
    summary = [f"{len(resp.results)} result{'s' if len(resp.results) != 1 else ''}"]
    if resp.weaker:
        summary.append(f"{len(resp.weaker)} weaker")
    if kinds:
        summary.append(f"only {kinds}")
    if scope_note:
        summary.append(scope_note)
    summary.append(f"{ms:.0f} ms")
    parts = [f'<div class="v-summary">{_e(" · ".join(summary))}</div>', visual_row(resp.visual, session)]
    cards = []
    for i, r in enumerate(rows, start=1):
        if r.weaker and (i == 1 or not rows[i - 2].weaker):
            cards.append('<div class="v-divider" role="separator"><span>Weaker matches</span></div>')
        cards.append(card(r, i, session, display, show_scores))
    if len(rows) > page:
        head, tail = _split_cards(cards, page)
        parts.append(f'<div class="v-list">{"".join(head)}</div>')
        parts.append(f'<div class="v-list v-more" hidden>{"".join(tail)}</div>')
        parts.append(f'<button type="button" class="v-showmore">Show more ({len(rows) - page})</button>')
    else:
        parts.append(f'<div class="v-list">{"".join(cards)}</div>')
    if show_scores:
        parts.append(f'<div class="v-scores v-debug">clip {_e(resp.clip)} · weights {_e(resp.weights)} · '
                     f"timings {_e(resp.timings_ms)}</div>")
    return "".join(parts)


def _split_cards(cards: list[str], page: int) -> tuple[list[str], list[str]]:
    """Split after the `page`-th card; a divider stays with the cards that follow it."""
    seen = 0
    for i, c in enumerate(cards):
        if c.startswith('<button type="button" class="v-card"'):
            seen += 1
            if seen == page:
                return cards[: i + 1], cards[i + 1:]
    return cards, []


def detail_html(d: Detail, session: str | None, *, page: int | None) -> str:
    """The detail panel: a larger preview (a PDF's matched page), the full matched chunk, every path."""
    preview = image_url("preview", d.file_id, session, page=page if d.kind == "pdf" else None)
    where = f"{'Slide' if d.kind == 'slides' else 'Page'} {d.page}" + (f" of {d.pages}" if d.pages else "") \
        if d.page else ""
    meta = [f'<span class="v-kind v-kind-{_e(d.kind)}">{_e(KIND_NAMES.get(d.kind, d.kind))}</span>']
    if where:
        meta.append(f'<span class="v-where">{_e(where)}</span>')
    if date := nice_date(d.effective_date):
        meta.append(f"<span>{_e(date)}</span>")
    chunk = (f'<h4>Matched text</h4><div class="v-chunk">{d.chunk_html}</div>' if d.chunk_html
             else '<p class="v-muted">Found by what the picture shows: no text matched.</p>')
    paths = "".join(f"<li><code>{_e(p)}</code></li>" for p in d.paths)
    label = "Locations" if len(d.paths) > 1 else "Location"
    return (
        f'<article class="v-detail"><h2>{_e(d.filename)}</h2><div class="v-meta">{"".join(meta)}</div>'
        f'<div class="v-preview"><img src="{_e(preview)}" alt="Preview of {_e(d.filename)}"></div>'
        f'{chunk}<h4>{label}</h4><ul class="v-paths">{paths}</ul></article>'
    )


# ---------------------------------------------------------------- upload and index tabs

UPLOAD_NOTICE = ("Uploads are processed on this server, kept only for your session, and deleted after 30 minutes. "
                 "For private use, run it on your own computer (see README).")


def upload_status_html(files: list, *, indexed_total: int, bytes_total: int, max_files: int, max_bytes_label: str,
                       report_added: int | None = None) -> str:
    """Per-file outcome of one upload (`vault.session.FileStatus` rows) and the session's running totals."""
    if not files:
        return ""
    labels = {"indexed": ("v-ok", "Indexed"), "duplicate": ("v-skip", "Already uploaded"),
              "rejected": ("v-err", "Not accepted"), "error": ("v-err", "Couldn't read")}
    counts: dict[str, int] = {}
    rows = []
    for f in files:
        cls, label = labels.get(f.status, ("", f.status))
        counts[label] = counts.get(label, 0) + 1
        rows.append(f'<tr><td>{_e(f.name)}</td><td class="{cls}">{_e(label)}</td><td>{_e(f.reason)}</td></tr>')
    summary = " · ".join(f"{n} {label.lower()}" for label, n in counts.items())
    usage = (f"Your session: {indexed_total} of {max_files} files, {_e(human_size(bytes_total))} of "
             f"{_e(max_bytes_label)}. Search them on the Search tab with <b>My uploads</b>.")
    return (f'<div class="v-panel"><h3>{_e(summary)}</h3><p class="v-muted">{usage}</p>'
            f'<table class="v-table"><tr><th>File</th><th>Result</th><th>Why</th></tr>{"".join(rows)}</table></div>')


def human_size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def stats_html(stats, display: Display = lambda p: p) -> str:
    """The Index tab: counts by kind, the last run, the folders and any files that failed."""
    tiles = "".join(f'<div class="v-stat"><b>{stats.counts.get(k, 0)}</b><span>{_e(KIND_LABELS[k])}</span></div>'
                    for k in KIND_LABELS)
    roots = "".join(f"<li><code>{_e(r)}</code></li>" for r in stats.roots) or "<li>No folders yet.</li>"
    last = ""
    if run := stats.last_run:
        fields = [("added", "added"), ("linked", "renamed or copied"), ("updated", "changed"), ("deleted", "removed"),
                  ("unchanged", "unchanged")]
        parts = [f"{run.get(k, 0)} {label}" for k, label in fields if run.get(k)]
        secs = sum((run.get("timings") or {}).values())
        last = (f'<p class="v-muted">Last run {_e(nice_date(stats.last_indexed))}: '
                f'{_e(", ".join(parts) or "nothing changed")}{f" in {secs:.0f} s" if secs >= 1 else ""}.</p>')
    errors = ""
    if stats.errors:
        rows = "".join(f'<tr><td>{_e(display(e["path"]))}</td><td class="v-err">{_e(e["status"])}</td>'
                       f'<td>{_e(e["error"])}</td></tr>' for e in stats.errors)
        errors = (f'<h3 style="margin-top:14px">Files that couldn\'t be indexed ({len(stats.errors)})</h3>'
                  f'<table class="v-table"><tr><th>File</th><th>Status</th><th>Why</th></tr>{rows}</table>')
    return (f'<div class="v-panel"><h3>Your index</h3><div class="v-stats">{tiles}</div>{last}'
            f'<h3 style="margin-top:14px">Folders</h3><ul class="v-paths">{roots}</ul>{errors}</div>')
