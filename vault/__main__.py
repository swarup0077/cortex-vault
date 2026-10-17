"""CLI: python -m vault index | reindex | search | stats (SPEC §13)."""

from __future__ import annotations

import argparse
import html
import logging
import re
import signal
import sqlite3
import sys
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from vault import db
from vault.config import Config
from vault.ingest.pipeline import IndexReport, index_paths, reindex
from vault.search.engine import search


@contextmanager
def graceful_stop(console: Console) -> Iterator[threading.Event]:
    """First Ctrl+C: finish the current file, then stop. Second Ctrl+C: abort now."""
    stop = threading.Event()

    def handler(signum, frame) -> None:
        if stop.is_set():
            raise KeyboardInterrupt
        stop.set()
        console.print("[yellow]Stopping after the current file… (Ctrl+C again to abort)")

    previous = signal.signal(signal.SIGINT, handler)
    try:
        yield stop
    finally:
        signal.signal(signal.SIGINT, previous)


def _run_index(console: Console, conn: sqlite3.Connection, cfg: Config, folders: Sequence[Path] | None) -> IndexReport:
    columns = (TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(),
               TextColumn("[dim]{task.fields[file]}"))
    with graceful_stop(console) as stop, Progress(*columns, console=console, transient=True) as bar:
        task = bar.add_task("Indexing", total=None, file="")

        def progress(done: int, total: int, path: str) -> None:
            bar.update(task, completed=done, total=total, file=Path(path).name if path else "")

        if folders is None:
            return reindex(conn, cfg, stop=stop, progress=progress)
        return index_paths(conn, folders, cfg, stop=stop, progress=progress)


def _print_report(console: Console, rep: IndexReport) -> None:
    t = Table(title="Index run" + (" (interrupted)" if rep.interrupted else ""), show_header=False)
    for label, value in [
        ("seen", rep.seen), ("unchanged", rep.unchanged), ("added", rep.added), ("linked", rep.linked),
        ("updated", rep.updated), ("deleted paths", rep.deleted), ("deleted files", rep.files_deleted),
        ("errors / skipped", len(rep.errors)),
    ]:
        t.add_row(label, str(value))
    for stage, secs in rep.timings.items():
        t.add_row(f"time: {stage}", f"{secs:.2f} s")
    for kind, fps in sorted(rep.files_per_sec().items()):
        t.add_row(f"files/sec: {kind}", f"{fps:.2f}")
    console.print(t)
    for root in rep.missing_roots:
        console.print(f"[yellow]Root not found (not swept): {escape(root)}")
    for e in rep.errors[:20]:
        console.print(f"[red]{e['status']}[/] {escape(e['path'])}: {escape(e['error'])}")
    if len(rep.errors) > 20:
        console.print(f"… and {len(rep.errors) - 20} more")


def _snippet_markup(snippet_html: str) -> str:
    parts = re.split(r"(<mark>.*?</mark>)", snippet_html)
    out = []
    for p in parts:
        if p.startswith("<mark>"):
            out.append(f"[bold yellow]{escape(html.unescape(p[6:-7]))}[/]")
        else:
            out.append(escape(html.unescape(p)))
    return "".join(out)


def cmd_search(console: Console, conn: sqlite3.Connection, cfg: Config, args: argparse.Namespace) -> int:
    resp = search(conn, args.query, cfg, limit=args.limit)
    if not resp.results:
        console.print("No results.")
    for i, r in enumerate(resp.results, start=1):
        where = f" [cyan]{'slide' if r.kind == 'slides' else 'page'} {r.page}[/]" if r.page else ""
        copies = f" [dim]+{r.copies} copies[/]" if r.copies else ""
        console.print(f"[bold]{i:>2}. {escape(r.filename)}[/]{where}  [dim]{r.kind} · {r.effective_date or ''}[/]{copies}")
        console.print(f"    [dim]{escape(r.folder)}[/]")
        if r.snippet_html:
            console.print(f"    {_snippet_markup(r.snippet_html)}")
        if args.debug:
            console.print(f"    [magenta]rrf={r.rrf_score:.4f} ranks={r.ranks} chunk={r.chunk_id}[/]")
    if args.debug:
        console.print(f"[magenta]parsed={resp.parsed}[/]")
        console.print(f"[magenta]timings ms={resp.timings_ms} total={sum(resp.timings_ms.values()):.1f}[/]")
    return 0


def cmd_stats(console: Console, conn: sqlite3.Connection, cfg: Config) -> int:
    console.print(f"[bold]Vault[/] {escape(str(cfg.db_path))}")
    t = Table()
    t.add_column("kind")
    t.add_column("ok", justify="right")
    t.add_column("error", justify="right")
    t.add_column("skipped", justify="right")
    rows = conn.execute("SELECT kind, status, count(*) FROM files GROUP BY kind, status").fetchall()
    by_kind: dict[str, dict[str, int]] = {}
    for kind, status, n in rows:
        by_kind.setdefault(kind, {})[status] = n
    for kind in sorted(by_kind):
        c = by_kind[kind]
        t.add_row(kind, *(str(c.get(s, 0)) for s in ("ok", "error", "skipped")))
    console.print(t)

    def count(table: str) -> int:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    size = cfg.db_path.stat().st_size if cfg.db_path.exists() else 0
    console.print(
        f"files {count('files')} · paths {count('paths')} · chunks {count('chunks')} · "
        f"text vectors {count('text_vectors')} · db {size / 2**20:.1f} MB"
    )
    console.print(f"text model: {db.get_meta(conn, 'text_model') or '-'}")
    console.print(f"last indexed: {db.get_meta(conn, 'last_indexed') or 'never'}")
    for root in db.get_roots(conn):
        console.print(f"root: {escape(root)}")
    errors = conn.execute(
        "SELECT p.path, f.status, f.error FROM files f JOIN paths p ON p.file_id = f.id WHERE f.status != 'ok' LIMIT 20"
    ).fetchall()
    for path, status, error in errors:
        console.print(f"[red]{status}[/] {escape(path)}: {escape(error or '')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m vault", description="Personal Search Vault")
    parser.add_argument("-v", "--verbose", action="store_true", help="info-level logging")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index", help="incrementally index folders")
    p.add_argument("folders", nargs="+", type=Path)
    sub.add_parser("reindex", help="wipe and rebuild every known folder (after a model change)")
    p = sub.add_parser("search", help="search the index")
    p.add_argument("query")
    p.add_argument("--debug", action="store_true", help="show ranks, scores and stage timings")
    p.add_argument("-n", "--limit", type=int, default=10)
    sub.add_parser("stats", help="counts, models, roots and errors")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
        force=True,
    )
    cfg = Config()
    conn = db.connect(cfg.db_path)
    try:
        if args.cmd in ("index", "reindex"):
            _print_report(console, _run_index(console, conn, cfg, args.folders if args.cmd == "index" else None))
            return 0
        if args.cmd == "search":
            return cmd_search(console, conn, cfg, args)
        return cmd_stats(console, conn, cfg)
    except db.ModelMismatchError as e:
        console.print(f"[red]{escape(str(e))}")
        return 2
    except KeyboardInterrupt:
        console.print("[red]Aborted.")
        return 130
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
