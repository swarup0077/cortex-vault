"""CLI: python -m vault index | reindex | search | stats | build-demo | scrub-exif (SPEC §13)."""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import signal
import sqlite3
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from vault import db
from vault.config import Config
from vault import eval as ev
from vault.demo import CORPUS_DIR, DEMO_DIR, build_demo, gps_files, strip_gps
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


@contextmanager
def _progress_bar(console: Console) -> Iterator[Callable[[int, int, str], None]]:
    columns = (TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(),
               TextColumn("[dim]{task.fields[file]}"))
    with Progress(*columns, console=console, transient=True) as bar:
        task = bar.add_task("Indexing", total=None, file="")

        def progress(done: int, total: int, path: str, phase: str = "read") -> None:
            bar.update(task, description="Listing" if phase == "list" else "Reading", completed=done, total=total,
                       file=Path(path).name if path else "")

        yield progress


def _run_index(console: Console, conn: sqlite3.Connection, cfg: Config, folders: Sequence[Path] | None) -> IndexReport:
    with graceful_stop(console) as stop, _progress_bar(console) as progress:
        if folders is None:
            return reindex(conn, cfg, stop=stop, progress=progress)
        return index_paths(conn, folders, cfg, stop=stop, progress=progress)


def _before_first_section(md: str, lines: list[str]) -> str:
    """`lines` inserted into a report before its first half's section."""
    head, sep, rest = md.partition("\n## ")
    return head + "\n" + "\n".join(lines) + sep + rest


def cmd_eval(console: Console, cfg: Config, args: argparse.Namespace) -> int:
    """`vault eval` (SPEC §9): import the owner's queries, or run them and write the report."""
    paths = ev.Paths()
    if args.import_csv:
        rep = ev.import_queries(args.import_csv, paths)
        ev.write_queries(rep.queries, paths)
        public = sum(q.public for q in rep.queries)
        console.print(f"{len(rep.queries)} queries ({public} public, {len(rep.queries) - public} private); "
                      f"{len(rep.left_out)} left out, {len(rep.trimmed)} lost some correct files")
        for qid, why in rep.left_out:
            console.print(f"  left out {qid}: {why}")
        for qid, n in rep.trimmed:
            console.print(f"  {qid}: {n} correct file{'s' if n != 1 else ''} left out, the rest kept")
        console.print(f"split: {paths.split_file.relative_to(paths.repo).as_posix()}, "
                      f"full set: {paths.full_queries.relative_to(paths.repo).as_posix()} (git-ignored)")
        return 0
    if args.import_generated:
        rep = ev.import_queries([args.import_generated], paths, sources=("generated",))
        ev.write_generated(rep.queries, paths)
        console.print(f"{len(rep.queries)} generated-file queries; {len(rep.left_out)} left out → "
                      f"{paths.generated_queries.relative_to(paths.repo).as_posix()}")
        return 0
    if args.queries == "generated":
        splits = [ev.GUARD]
    else:
        splits = ["tune", "heldout"] if args.split == "all" else [args.split]
    if "heldout" in splits and not args.purpose:
        console.print("[red]A run on the held-out half needs --purpose baseline or --purpose final (SPEC §9).")
        return 2
    if args.extra:
        paths = replace(paths, index=paths.repo / "data" / "eval_scale_index")
    gen = "_generated" if args.queries == "generated" else ""
    out = args.out or paths.out / ("results" + gen + {"divider-rerun": "_divider", "scale": "_scale"}.get(
        args.purpose, ""))
    overrides = ev.parse_settings(args.set)
    lib = ev.eval_library(cfg, paths, index=not args.no_index, extra=args.extra)
    queries = [q for q in ev.load_queries(paths, args.queries) if q.split in splits]
    rows = list(ev.ABLATIONS) if args.rows == "all" and args.purpose != "divider-rerun" else [ev.APP]
    run = ev.evaluate(lib, queries, overrides, rows=rows, paths=paths)
    if args.purpose == "divider-rerun":
        final = json.loads((paths.out / f"results{gen}.json").read_text(encoding="utf-8"))
        md, data = ev.divider_rerun(run, final, splits)
        ev.write_report(md, data, out)
        if "heldout" in splits:
            ev.log_heldout(paths, args.purpose, overrides)
        console.print(md)
        console.print(f"wrote {out.with_suffix('.md')} and .json")
        return 0 if data["identical"] else 1
    detail = splits if args.purpose == "final" else [s for s in splits if s in ("tune", ev.GUARD)]
    title = {"baseline": "Eval baseline", "final": "Eval: final settings",
             "scale": "Eval at scale: the eval files plus generated extras"}.get(args.purpose, "Eval (tuning half)")
    if args.queries == "generated":
        title = {"baseline": "Generated-file set: baseline", "final": "Generated-file set: final settings"}.get(
            args.purpose, "Generated-file set")
    md, data = ev.report(run, splits=splits, detail=detail, title=title)
    if args.purpose == "scale" and (paths.out / f"results{gen}.json").exists():
        final = ev.with_current_divider(json.loads((paths.out / f"results{gen}.json").read_text(encoding="utf-8")))
        lines, data["final_vs_scale"] = ev.compare(final, data, before="final", after="at scale")
        md = _before_first_section(md, lines)
    baseline = args.baseline or (paths.out / ("results_generated_baseline.json" if args.queries == "generated"
                                              else "results_baseline.json"))
    if args.purpose == "final" and baseline.exists():
        lines, data["baseline_vs_final"] = ev.compare(json.loads(baseline.read_text(encoding="utf-8")), data)
        md = _before_first_section(md, lines)
    ev.write_report(md, data, out)
    if "heldout" in splits:
        finals = ev.log_heldout(paths, args.purpose, overrides)
        if args.purpose == "final" and finals > 1:
            console.print(f"[yellow]This is final run number {finals} on the held-out half (see "
                          f"{paths.heldout_log.relative_to(paths.repo).as_posix()}).")
    table = Table("half", "setting", "n", "R@1", "R@5", "R@10", "MRR@10", "above divider (median)")
    for name, entry in data["splits"].items():
        for row, v in entry["rows"].items():
            m = v["overall"]
            table.add_row(name, row, str(m["n"]), str(m["r1"]), str(m["r5"]), str(m["r10"]), f"{m['mrr']:.3f}",
                          str(entry["divider"]["median_above"]) if row == ev.APP else "")
    console.print(table)
    console.print(f"wrote {out.with_suffix('.md')} and .json")
    return 0


def cmd_build_demo(console: Console, cfg: Config, args: argparse.Namespace) -> int:
    try:
        with _progress_bar(console) as progress:
            rep = build_demo(args.corpus, args.out, cfg, progress=progress)
    except ValueError as e:
        console.print(f"[red]{escape(str(e))}")
        return 1
    _print_report(console, rep)
    console.print(f"Demo index written to {escape(str(args.out))}")
    return 0


def cmd_scrub_exif(console: Console, args: argparse.Namespace) -> int:
    found = gps_files(args.folder)
    for path in found:
        strip_gps(path)
        console.print(f"GPS removed: {escape(str(path))}")
    console.print(f"{len(found)} image(s) had GPS EXIF")
    return 0


def _print_report(console: Console, rep: IndexReport) -> None:
    t = Table(title="Index run" + (" (interrupted)" if rep.interrupted else ""), show_header=False)
    for label, value in [
        ("seen", rep.seen), ("unchanged", rep.unchanged), ("added", rep.added), ("linked", rep.linked),
        ("updated", rep.updated), ("deleted paths", rep.deleted), ("deleted files", rep.files_deleted),
        ("listed (name, native text)", rep.listed), ("read fully (OCR, meaning, pictures)", rep.completed),
        ("errors / skipped", len(rep.errors)), ("OCR'd images + PDF pages", rep.ocr_images),
        ("  of which no text", rep.ocr_empty),
        ("older files brought up to date", rep.backfilled),
        ("images whose kind CLIP decided", rep.reclassified), ("images re-OCR'd (angle setting)", rep.reocr),
    ]:
        t.add_row(label, str(value))
    for stage, secs in rep.timings.items():
        t.add_row(f"time: {stage}", f"{secs:.2f} s")
    for kind, fps in sorted(rep.files_per_sec().items()):
        t.add_row(f"files/sec: {kind}", f"{fps:.2f}")
    console.print(t)
    for path in rep.ocr_capped:
        console.print(f"[yellow]OCR page cap hit (later pages keep native text only): {escape(path)}")
    for path in rep.ocr_stopped:
        console.print(f"[yellow]OCR stopped early, its first pages were unreadable (handwriting?): {escape(path)}")
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
    if not resp.results and not resp.weaker:
        console.print("No results.")
    for i, r in enumerate(resp.results + resp.weaker, start=1):
        if i == len(resp.results) + 1:  # the first weaker match
            console.print("[dim]── weaker matches ──[/]")
        where = f" [cyan]{'slide' if r.kind == 'slides' else 'page'} {r.page}[/]" if r.page else ""
        copies = f" [dim]+{r.copies} copies[/]" if r.copies else ""
        console.print(f"[bold]{i:>2}. {escape(r.filename)}[/]{where}  [dim]{r.kind} · {r.effective_date or ''}[/]{copies}")
        console.print(f"    [dim]{escape(r.folder)}[/]")
        if r.snippet_html:
            console.print(f"    {_snippet_markup(r.snippet_html)}")
        if args.debug:
            shares = {k: round(v, 4) for k, v in r.shares.items()}
            console.print(f"    [magenta]rrf={r.rrf_score:.4f} ranks={r.ranks} shares={shares} "
                          f"clip={r.clip_status} chunk={r.chunk_id}[/]")
    if args.debug:
        console.print(f"[magenta]parsed={resp.parsed}[/]")
        console.print(f"[magenta]clip={resp.clip}[/]")
        console.print(f"[magenta]weights={resp.weights}[/]")
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
        f"text vectors {count('text_vectors')} · image vectors {count('image_vectors')} · "
        f"OCR lines {count('ocr_lines')} · db {size / 2**20:.1f} MB"
    )
    console.print(f"text model: {db.get_meta(conn, 'text_model') or '-'}")
    console.print(f"CLIP model: {db.get_meta(conn, 'clip_model') or '-'}")
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
    p = sub.add_parser("build-demo", help="build demo_index/ from corpus/ (repo-relative paths, no GPS)")
    p.add_argument("--corpus", type=Path, default=CORPUS_DIR)
    p.add_argument("--out", type=Path, default=DEMO_DIR)
    p = sub.add_parser("scrub-exif", help="remove GPS EXIF from the images in a folder")
    p.add_argument("folder", type=Path)
    p = sub.add_parser("eval", help="run the eval (SPEC §9): eval/results.md and .json")
    p.add_argument("--import", dest="import_csv", nargs="+", type=Path, metavar="CSV",
                   help="convert the owner's queries.csv (then queries_extra.csv) and split them; nothing is run")
    p.add_argument("--import-generated", type=Path, metavar="CSV",
                   help="convert the approved generated-file query set (eval/queries_generated.csv)")
    p.add_argument("--queries", choices=("owner", "generated"), default="owner",
                   help="the owner's set (headline numbers) or the generated-file set (a guardrail, reported apart)")
    p.add_argument("--split", choices=("tune", "heldout", "all"), default="tune")
    p.add_argument("--purpose", choices=ev.PURPOSES, help="required when the held-out half runs: baseline, final, "
                   "divider-rerun (divider numbers only, ranks checked against the final run) or scale")
    p.add_argument("--out", type=Path, help="report path without suffix (default: eval/results, "
                   "eval/results_divider for divider-rerun, eval/results_scale for scale)")
    p.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="a setting to change")
    p.add_argument("--rows", choices=("app", "all"), default="all", help="the app only, or every ablation row")
    p.add_argument("--no-index", action="store_true", help="use the eval index as it is")
    p.add_argument("--extra", type=Path, nargs="*", default=[], help="more folders indexed with the eval files "
                   "(the scale test); the index then lives in data/eval_scale_index/")
    p.add_argument("--baseline", type=Path, help="a final run compares with this report's .json (default: "
                   "eval/results_baseline.json, or results_generated_baseline.json)")
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
    if args.cmd == "build-demo":
        return cmd_build_demo(console, cfg, args)
    if args.cmd == "scrub-exif":
        return cmd_scrub_exif(console, args)
    if args.cmd == "eval":
        return cmd_eval(console, cfg, args)
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
