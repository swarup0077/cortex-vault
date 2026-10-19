"""Evaluation (SPEC §9): `python -m vault eval`.

The owner's queries (`queries.csv`, written before any search) are converted once by `vault eval --import`: each
correct file becomes a key, its repo-relative path in the public corpus or a neutral id (private-01, …) for the
private set, which lives only in the git-ignored `data/eval_private/` together with the id-to-name map. A correct
file the owner left out is dropped from its query, and a query with none left is left out. Each query gets a fixed
tuning or held-out half, stratified by search type with a fixed seed, per source file, so queries added later never
move the earlier ones. Committed files hold the public queries only (`eval/queries.jsonl`) and the split by query
id (`eval/split.json`); the full set stays in `data/eval_private/`.

A run indexes `corpus/` and the private files into `data/eval_index/` (incrementally), runs every query under the
app's settings and the ablation rows, and writes `<out>.md` and `<out>.json`: Recall@1/5/10 and MRR@10 overall, by
search type, by file type and by set (public, private), the weaker-matches divider (results above it, and whether
every correct file is), latency, and the queries that missed. Committed output names private files and queries by
id only. Per-query detail for the held-out half is written only by the final and post-z runs, and every run that
touches that half is logged in `eval/heldout_runs.jsonl`. The report without a suffix (`eval/results.md`) is the
current settings'; it opens with every logged held-out run side by side.
"""

from __future__ import annotations

import csv
import json
import random
import statistics
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime
from pathlib import Path

from vault.config import REPO_ROOT, Config
from vault.demo import CORPUS_DIR
from vault.library import TEXT_SUFFIXES, Library
from vault.search.engine import SearchResponse, SearchResult, search
from vault.search.retrievers import load_text_index

SEED = 20261019
SOURCES = ("owner", "owner-extra")  # queries.csv, then queries_extra.csv: their ids start with q and x
PURPOSES = ("baseline", "final", "divider-rerun", "scale", "post-z", "coverage")  # why a run is logged
# Each purpose's report, after "results" (and "_generated" for the generated set): the unsuffixed report is the
# current settings' (post-z since item z, 19 Oct); a final run's is kept apart, and what a run compares with.
REPORT = {None: "", "baseline": "_baseline", "final": "_final", "divider-rerun": "_divider", "scale": "_scale",
          "post-z": "", "coverage": "_coverage"}
COMPARE_WITH = {"final": "_baseline", "post-z": "_final", "scale": ""}
GUARD = "guard"  # the split of the generated-file set: reported apart, a guardrail in tuning, never tuned toward
# The split of the coverage set: more queries about the generated files, for the §9 coverage the owner's own
# queries don't aim at (Word and PowerPoint targets, kind words, mixed queries). Run once, logged; never tuned on.
COVERAGE = "coverage"
METHODS = ("bm25", "all_terms", "dense", "clip")
CUTOFFS = (1, 5, 10)


@dataclass(frozen=True)
class Paths:
    repo: Path = REPO_ROOT
    corpus: Path = CORPUS_DIR
    private: Path = REPO_ROOT / "data" / "eval_private"  # git-ignored: the private files, ids.csv, full queries
    index: Path = REPO_ROOT / "data" / "eval_index"  # git-ignored: corpus/ plus the private files
    out: Path = REPO_ROOT / "eval"

    @property
    def private_files(self) -> Path:
        return self.private / "files"

    @property
    def ids_file(self) -> Path:
        return self.private / "ids.csv"

    @property
    def full_queries(self) -> Path:
        return self.private / "queries_full.jsonl"

    @property
    def public_queries(self) -> Path:
        return self.out / "queries.jsonl"

    @property
    def generated_queries(self) -> Path:
        return self.out / "queries_generated.jsonl"

    @property
    def coverage_queries(self) -> Path:
        return self.out / "queries_coverage.jsonl"

    @property
    def split_file(self) -> Path:
        return self.out / "split.json"

    @property
    def heldout_log(self) -> Path:
        return self.out / "heldout_runs.jsonl"


@dataclass
class Query:
    id: str
    text: str
    category: str  # the owner's search_type: "meaning", "picture", "exact word", "inside a scan"
    relevant: list[str]  # keys of the correct files
    split: str = ""  # "tune" | "heldout"
    source: str = "owner"

    @property
    def public(self) -> bool:
        return not any(is_private(k) for k in self.relevant)


def is_private(key: str) -> bool:
    return key.startswith("private-")


# ---------------------------------------------------------------- import and split


def private_ids(paths: Paths) -> dict[str, str]:
    """name → neutral id, from the git-ignored map."""
    if not paths.ids_file.is_file():
        return {}
    with paths.ids_file.open(encoding="utf-8", newline="") as f:
        return {row["name"]: row["id"] for row in csv.DictReader(f)}


@dataclass
class ImportReport:
    queries: list[Query]
    left_out: list[tuple[str, str]] = field(default_factory=list)  # (query id, why)
    trimmed: list[tuple[str, int]] = field(default_factory=list)  # (query id, correct files dropped)


def import_queries(csvs: Sequence[Path], paths: Paths = Paths(), seed: int = SEED, *,
                   sources: Sequence[str] = SOURCES) -> ImportReport:
    """The owner's CSVs (query, expected_file, search_type; several files separated by "|") → queries with keys.
    The first CSV is `owner` (ids q01…), the second `owner-extra` (x01…); `sources=("generated",)` reads the
    generated-file set (g01…) and `("coverage",)` the coverage set (c01…), which aren't split. The CSVs are read, never changed."""
    ids = private_ids(paths)
    public = {p.name: p.relative_to(paths.repo).as_posix() for p in paths.corpus.rglob("*") if p.is_file()}
    report = ImportReport([])
    for source, csv_path in zip(sources, csvs):
        prefix = {"owner": "q", "owner-extra": "x", "generated": "g", "coverage": "c"}[source]
        with Path(csv_path).open(encoding="utf-8-sig", newline="") as f:
            for i, row in enumerate(csv.DictReader(f), start=1):
                qid = f"{prefix}{i:02d}"
                names = [n.strip() for n in row["expected_file"].split("|") if n.strip()]
                keys = [ids[n] if n in ids else public[n] for n in names if n in ids or n in public]
                if not keys:
                    report.left_out.append((qid, "every correct file was left out of the corpus"))
                    continue
                if len(keys) < len(names):
                    report.trimmed.append((qid, len(names) - len(keys)))
                report.queries.append(Query(qid, row["query"].strip(), row["search_type"].strip(), keys,
                                            source=source))
    if "generated" in sources or "coverage" in sources:
        for q in report.queries:
            q.split = GUARD if q.source == "generated" else COVERAGE
    else:
        assign_splits(report.queries, seed)
    return report


def assign_splits(queries: list[Query], seed: int = SEED) -> None:
    """Half tuning, half held out, within each source and search type (shuffled with `seed`). Odd groups give
    their extra query to each half in turn, so the halves stay level overall."""
    rng = random.Random(seed)
    extra_to_tune = True
    for source in SOURCES:
        for category in sorted({q.category for q in queries if q.source == source}):
            group = sorted((q for q in queries if q.source == source and q.category == category), key=lambda q: q.id)
            rng.shuffle(group)
            half = len(group) // 2 + (len(group) % 2 if extra_to_tune else 0)
            if len(group) % 2:
                extra_to_tune = not extra_to_tune
            for j, q in enumerate(group):
                q.split = "tune" if j < half else "heldout"


def write_queries(queries: Sequence[Query], paths: Paths = Paths()) -> None:
    """The full set (private, git-ignored), the public queries and the split by id (committed)."""
    paths.private.mkdir(parents=True, exist_ok=True)
    paths.out.mkdir(parents=True, exist_ok=True)
    with paths.full_queries.open("w", encoding="utf-8") as f:
        for q in queries:
            f.write(json.dumps(asdict(q), ensure_ascii=False) + "\n")
    with paths.public_queries.open("w", encoding="utf-8") as f:
        for q in queries:
            if q.public:
                f.write(json.dumps(asdict(q), ensure_ascii=False) + "\n")
    counts: dict[str, dict[str, int]] = {}
    for q in queries:
        counts.setdefault(q.category, {"tune": 0, "heldout": 0})[q.split] += 1
    paths.split_file.write_text(json.dumps({"seed": SEED, "stratified_by": "search_type, per source file",
                                            "counts": counts, "splits": {q.id: q.split for q in queries}},
                                           indent=1) + "\n", encoding="utf-8")


def write_generated(queries: Sequence[Query], paths: Paths = Paths(), which: str = "generated") -> None:
    paths.out.mkdir(parents=True, exist_ok=True)
    with (paths.coverage_queries if which == "coverage" else paths.generated_queries).open("w", encoding="utf-8") as f:
        for q in queries:
            f.write(json.dumps(asdict(q), ensure_ascii=False) + "\n")


def load_queries(paths: Paths = Paths(), which: str = "owner") -> list[Query]:
    """The owner's set (the full one when this machine has it, else the committed public queries), or the
    generated-file set, or the coverage set."""
    if which == "generated":
        src = paths.generated_queries
    elif which == "coverage":
        src = paths.coverage_queries
    else:
        src = paths.full_queries if paths.full_queries.is_file() else paths.public_queries
    with src.open(encoding="utf-8") as f:
        return [Query(**json.loads(line)) for line in f if line.strip()]


# ---------------------------------------------------------------- the eval index and keys


def key_of(stored: str, paths: Paths, ids: dict[str, str]) -> str:
    """A stored path → its key: repo-relative for the corpus, the neutral id for a private file."""
    p = Path(stored)
    if not p.is_absolute():
        p = paths.repo / p
    if p.is_relative_to(paths.private_files):
        return ids.get(p.name, "private-unknown")
    if p.is_relative_to(paths.repo):
        return p.relative_to(paths.repo).as_posix()
    return p.name


def eval_library(cfg: Config, paths: Paths = Paths(), *, index: bool = True, extra: Sequence[Path] = ()) -> Library:
    """corpus/ plus the private files (when this machine has them), indexed incrementally into `paths.index`; `extra`
    folders are indexed with them (the scale test's generated files, never a correct file)."""
    lib = Library(replace(cfg, data_dir=paths.index))
    if index:
        roots = [paths.corpus] + ([paths.private_files] if paths.private_files.is_dir() else []) + list(extra)
        lib.index(roots)
    return lib


def file_types(lib: Library, paths: Paths, ids: dict[str, str]) -> dict[str, str]:
    """key → the Archive's file type (screenshot, pdf, text, photo, doc, slides)."""
    out = {}
    with lib.connect() as conn:
        for path, kind in conn.execute("SELECT p.path, f.kind FROM paths p JOIN files f ON f.id = p.file_id"):
            t = "text" if kind == "doc" and Path(path).suffix.lower() in TEXT_SUFFIXES else kind
            out[key_of(path, paths, ids)] = t
    return out


# ---------------------------------------------------------------- one query


@dataclass
class Outcome:
    """One query under one setting."""

    rank: int | None  # position of the first correct file in the list as shown (results, then weaker), from 1
    above: int  # results above the weaker-matches divider
    all_above: bool  # every correct file is above the divider
    methods: dict[str, int]  # the first correct file found: each method's rank for it
    ms: float  # the engine's stage timers, summed


def outcome(resp: SearchResponse, relevant: Sequence[str], key: Callable[[str], str]) -> Outcome:
    def keys(r: SearchResult) -> set[str]:
        return {key(p) for p in r.paths}

    wanted = set(relevant)
    shown = [*resp.results, *resp.weaker]
    rank, methods = None, {}
    for i, r in enumerate(shown, start=1):
        if keys(r) & wanted:
            rank, methods = i, {m: v for m, v in r.ranks.items() if v}
            break
    above_keys = set().union(*(keys(r) for r in resp.results)) if resp.results else set()
    return Outcome(rank, len(resp.results), wanted <= above_keys, methods, round(sum(resp.timings_ms.values()), 1))


# ---------------------------------------------------------------- settings and ablations (SPEC §9)


def _weights(bm25: float, dense: float, clip: float, all_terms: float | None = None) -> dict[str, float]:
    return {"bm25": bm25, "all_terms": bm25 if all_terms is None else all_terms, "dense": dense, "clip": clip}


APP = "6. All three, CLIP gated (the app)"
ABLATIONS: dict[str, dict] = {  # "BM25" includes the every-word list (SPEC §9); row 8 (rerank) is Later
    "1. BM25 only": {"weights": _weights(1, 0, 0)},
    "2. Dense only": {"weights": _weights(0, 1, 0)},
    "3. CLIP only": {"weights": _weights(0, 0, 1)},
    "4. BM25 + dense": {"weights": _weights(1, 1, 0)},
    "5. All three, CLIP ungated": {"clip_gated": False},
    APP: {},
    "7. All three, gated, no every-word list": {"weights": _weights(1, 1, 1, all_terms=0)},
}
SINGLE = {"bm25": "1. BM25 only", "dense": "2. Dense only", "clip": "3. CLIP only"}


def parse_settings(pairs: Iterable[str]) -> dict:
    """`name=value` overrides of Config fields (`weights.dense=0.5` for one list's weight), typed like the field."""
    types = {f.name: f.type for f in fields(Config)}
    out: dict = {}
    for pair in pairs:
        name, _, raw = pair.partition("=")
        name = name.strip()
        if name.startswith("weights."):
            out.setdefault("weights", dict(Config().weights))[name.split(".", 1)[1]] = float(raw)
            continue
        if name not in types:
            raise ValueError(f"not a setting: {name}")
        default = getattr(Config(), name)
        if isinstance(default, bool):
            out[name] = raw.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(default, int):
            out[name] = int(raw)
        elif isinstance(default, float):
            out[name] = float(raw)
        else:
            out[name] = raw
    return out


def configure(cfg: Config, overrides: dict) -> Config:
    over = dict(overrides)
    if "weights" in over:
        over["weights"] = {**cfg.weights, **over["weights"]}
    return replace(cfg, **over)


# ---------------------------------------------------------------- metrics


def metrics(outcomes: Sequence[Outcome]) -> dict:
    n = len(outcomes)
    out: dict = {"n": n}
    for k in CUTOFFS:
        hits = sum(o.rank is not None and o.rank <= k for o in outcomes)
        out[f"r{k}"] = hits
        out[f"r{k}_pct"] = round(100 * hits / n, 1) if n else 0.0
    out["mrr"] = round(sum(1 / o.rank for o in outcomes if o.rank is not None and o.rank <= 10) / n, 3) if n else 0.0
    return out


def percentile(values: Sequence[float], q: float) -> float:
    vals = sorted(values)
    if not vals:
        return 0.0
    i = min(len(vals) - 1, max(0, round(q * (len(vals) - 1))))
    return vals[i]


# ---------------------------------------------------------------- a run


@dataclass
class Run:
    queries: list[Query]
    types: dict[str, str]  # key → file type
    rows: dict[str, dict[str, Outcome]]  # ablation row → query id → outcome
    settings: dict
    corpus: dict
    when: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def outcomes(self, row: str, qs: Iterable[Query]) -> list[Outcome]:
        return [self.rows[row][q.id] for q in qs]

    def groups(self, qs: Sequence[Query]) -> dict[str, dict[str, list[Query]]]:
        by: dict[str, dict[str, list[Query]]] = {"search type": {}, "file type": {}, "set": {}}
        for q in qs:
            by["search type"].setdefault(q.category, []).append(q)
            by["file type"].setdefault(self.types.get(q.relevant[0], "missing"), []).append(q)
            by["set"].setdefault("public" if q.public else "private", []).append(q)
        return by


def evaluate(lib: Library, queries: Sequence[Query], overrides: dict | None = None, *, rows: Sequence[str] = (),
             paths: Paths = Paths()) -> Run:
    """Every query under the app's settings (with `overrides`) and each ablation row in `rows`."""
    ids = private_ids(paths)

    def key(stored: str) -> str:
        return key_of(stored, paths, ids)

    base = configure(lib.cfg, overrides or {})
    out: dict[str, dict[str, Outcome]] = {}
    with lib.connect() as conn:
        text_index, image_index = lib.vectors(conn)
        if (base.dense_min_word, base.toc_min_entries) != (lib.cfg.dense_min_word, lib.cfg.toc_min_entries):
            # a run that changes which chunks dense search sees
            text_index = load_text_index(conn, text_index.matrix.shape[1], base.dense_min_word, base.toc_min_entries)
        for row in [APP, *[r for r in rows if r != APP]]:
            cfg = configure(base, ABLATIONS[row])
            out[row] = {q.id: outcome(search(conn, q.text, cfg, text_index=text_index, image_index=image_index),
                                      q.relevant, key) for q in queries}
        n_files = conn.execute("SELECT count(*) FROM files WHERE status = 'ok'").fetchone()[0]
        n_private = sum(1 for (p,) in conn.execute("SELECT path FROM paths") if is_private(key(p)))
    corpus = {"files": n_files, "private_paths": n_private, "public_paths": None}
    corpus["public_paths"] = sum(1 for k in file_types(lib, paths, ids) if not is_private(k))
    return Run(list(queries), file_types(lib, paths, ids), out, overrides or {}, corpus)


def summary(run: Run, qs: Sequence[Query]) -> dict:
    """Metrics per row, overall and by group; the divider and latency for the app's row."""
    rows = {}
    for row in run.rows:
        rows[row] = {"overall": metrics(run.outcomes(row, qs))}
        for by, groups in run.groups(qs).items():
            rows[row][by] = {name: metrics(run.outcomes(row, g)) for name, g in sorted(groups.items())}
    app = run.outcomes(APP, qs)
    return {
        "rows": rows,
        "divider": {**_divider(app), "set": {name: _divider(run.outcomes(APP, g))
                                             for name, g in sorted(run.groups(qs)["set"].items())}},
        "latency_ms": {"p50": percentile([o.ms for o in app], 0.5), "p95": percentile([o.ms for o in app], 0.95)},
    }


def _divider(app: Sequence[Outcome]) -> dict:
    return {"median_above": statistics.median([o.above for o in app]) if app else 0,
            "all_correct_above": sum(o.all_above for o in app), "of": len(app)}


def per_query(run: Run, qs: Sequence[Query]) -> list[dict]:
    """Each query's outcome under the app's settings, with each single method's rank for its correct file.
    A private query is named by id only."""
    out = []
    for q in qs:
        o = run.rows[APP][q.id]
        single = {m: run.rows[r][q.id].rank for m, r in SINGLE.items() if r in run.rows}
        out.append({"id": q.id, "split": q.split, "search_type": q.category, "set": "public" if q.public else "private",
                    "query": q.text if q.public else None, "relevant": q.relevant,
                    "file_type": run.types.get(q.relevant[0], "missing"), "rank": o.rank, "above_divider": o.above,
                    "all_correct_above": o.all_above, "found_by": o.methods, "single_method_rank": single})
    return out


# ---------------------------------------------------------------- reports


def _cell(m: dict, k: int) -> str:
    return f"{m[f'r{k}']}/{m['n']} ({m[f'r{k}_pct']:.0f}%)"


def _table(title: str, metrics_by_name: dict[str, dict]) -> list[str]:
    lines = [f"| {title} | n | R@1 | R@5 | R@10 | MRR@10 |", "|---|---|---|---|---|---|"]
    for name, m in metrics_by_name.items():
        lines.append(f"| {name} | {m['n']} | {_cell(m, 1)} | {_cell(m, 5)} | {_cell(m, 10)} | {m['mrr']:.3f} |")
    return lines


def report(run: Run, *, splits: Sequence[str], detail: Sequence[str], title: str, notes: Sequence[str] = (),
           purpose: str | None = None) -> tuple[str, dict]:
    """Markdown and JSON for `splits` (each, and "all" when there are several); per-query detail only for the
    halves in `detail`."""
    by_split = {s: [q for q in run.queries if q.split == s] for s in splits}
    if len(splits) > 1:
        by_split["all"] = [q for q in run.queries if q.split in splits]
    data = {"title": title, "purpose": purpose, "when": run.when, "corpus": run.corpus, "settings": run.settings,
            "models": _models(), "commit": _commit(), "splits": {}}
    md = [f"# {title}", "", f"Run {run.when} at commit {data['commit']}. Corpus: {run.corpus['files']} files "
          f"({run.corpus['public_paths']} public paths, {run.corpus['private_paths']} private). Models: "
          + ", ".join(data["models"].values()) + ".", ""]
    if run.settings:
        md += ["Settings changed from the app's: " + ", ".join(f"`{k}={v}`" for k, v in run.settings.items()), ""]
    md += [f"- {n}" for n in notes] + ([""] if notes else [])
    for name, qs in by_split.items():
        s = summary(run, qs)
        entry = {**s}
        if name in detail or (name == "all" and all(x in detail for x in splits)):
            entry["queries"] = per_query(run, qs)
        data["splits"][name] = entry
        app = s["rows"][APP]
        heading = {"tune": "Tuning half", "heldout": "Held-out half", GUARD: "Generated-file set",
                   COVERAGE: "Coverage queries (generated files)"}.get(name, "Both halves")
        md += [f"## {heading} ({len(qs)} queries)", ""]
        md += _table("Setting", {row: v["overall"] for row, v in s["rows"].items()}) + [""]
        for by in ("search type", "file type", "set"):
            md += _table(f"App, by {by}", app[by]) + [""]
        d = s["divider"]
        by_set = "; ".join(f"{k} {v['median_above']}, {v['all_correct_above']} of {v['of']}" for k, v in d["set"].items())
        md += [f"Weaker-matches divider (app): median {d['median_above']} results above it; every correct file above "
               f"it for {d['all_correct_above']} of {d['of']} queries ({by_set}). Latency: p50 "
               f"{s['latency_ms']['p50']:.0f} ms, p95 {s['latency_ms']['p95']:.0f} ms (engine stage timers).", ""]
        if "queries" in entry:
            misses = [q for q in entry["queries"] if q["rank"] != 1]
            md += [f"Queries without a correct file at #1 ({len(misses)}). Found by: each method's rank for the first "
                   "correct file in the app (for a weaker match, in the method's full list); single-method rank: its "
                   "rank with that method alone.", "",
                   "| id | set | search type | query | rank | above the divider | found by (app) | single-method rank |",
                   "|---|---|---|---|---|---|---|---|"]
            for q in misses:
                found = ", ".join(f"{m} #{r}" for m, r in q["found_by"].items()) or "–"
                single = ", ".join(f"{m} {r if r else '–'}" for m, r in q["single_method_rank"].items())
                md.append(f"| {q['id']} | {q['set']} | {q['search_type']} | {q['query'] or '(private)'} | "
                          f"{q['rank'] or 'not in 50'} | {'yes' if q['all_correct_above'] else 'no'} | {found} | "
                          f"{single} |")
            md.append("")
    return "\n".join(md), data


def compare(base: dict, data: dict, before: str = "baseline", after: str = "final") -> tuple[list[str], dict]:
    """An earlier run (`before`) vs this one, the app's row: each half overall and by set, and the divider."""
    md = [f"## {before[0].upper() + before[1:]} vs {after}", "",
          f"The {before} run {base['when']} at commit {base['commit']}; this run {data['when']} at commit "
          f"{data['commit']}." + (" The divider by set wasn't recorded by the baseline." if before == "baseline" else ""),
          "",
          "| half | queries | n | R@1 | R@5 | R@10 | MRR@10 | above the divider (median) | every correct file above it |",
          "|---|---|---|---|---|---|---|---|---|"]
    out: dict = {}
    names = {"tune": "Tuning half", "heldout": "Held-out half", "all": "Both halves", GUARD: "Generated-file set"}
    for name, entry in data["splits"].items():
        b = base["splits"].get(name)
        if not b:
            continue
        app, b_app = entry["rows"][APP], b["rows"][APP]
        out[name] = {before: {"overall": b_app["overall"], "set": b_app["set"], "divider": b["divider"]},
                     after: {"overall": app["overall"], "set": app["set"], "divider": entry["divider"]}}
        for label, m, bm in [("all", app["overall"], b_app["overall"]),
                             *[(k, app["set"][k], b_app["set"].get(k)) for k in sorted(app["set"])]]:
            if bm is None:
                continue
            cells = [f"{bm[f'r{k}']} → {m[f'r{k}']} ({bm[f'r{k}_pct']:.0f}% → {m[f'r{k}_pct']:.0f}%)" for k in CUTOFFS]
            d, bd = entry["divider"], b["divider"]
            div = ([f"{bd['median_above']} → {d['median_above']}",
                    f"{bd['all_correct_above']} → {d['all_correct_above']} of {d['of']}"] if label == "all" else ["", ""])
            md.append(f"| {names.get(name, name)} | {label} | {m['n']} | " + " | ".join(cells)
                      + f" | {bm['mrr']:.3f} → {m['mrr']:.3f} | " + " | ".join(div) + " |")
    return md + [""], out


def with_current_divider(final: dict) -> dict:
    """The final run's report with the divider's numbers from the rerun made after its rule changed, if there was one
    (the ranking is the same), so a later run compares like with like."""
    changed = final.get("divider_changed_after_final")
    if not changed:
        return final
    out = {**final, "splits": {k: dict(v) for k, v in final["splits"].items()}}
    for name, v in changed["splits"].items():
        if name in out["splits"]:
            out["splits"][name]["divider"] = v["divider"]
    return out


def divider_rerun(run: Run, final: dict, splits: Sequence[str]) -> tuple[str, dict]:
    """The divider's numbers only, for a run whose ranking must equal the final run's (a divider change moves no
    rank): each split's R@1/5/10 and MRR and every query's rank are checked against `final`."""
    by_split = {s: [q for q in run.queries if q.split == s] for s in splits}
    if len(splits) > 1:
        by_split["all"] = [q for q in run.queries if q.split in splits]
    data: dict = {"title": "Divider rerun", "when": run.when, "commit": _commit(), "settings": run.settings,
                  "final_run": {"when": final["when"], "commit": final["commit"]}, "splits": {}, "identical": True}
    md = ["# Divider rerun", "", f"Run {run.when} at commit {data['commit']}, against the final run "
          f"{final['when']} at commit {final['commit']}. Only the divider's numbers are reported; the ranking is "
          "checked against the final run's.", "",
          "| half | queries | R@1, R@5, R@10, MRR@10 as in the final run | every query's rank as in the final run | "
          "above the divider (median) | every correct file above it |", "|---|---|---|---|---|---|"]
    names = {"tune": "Tuning half", "heldout": "Held-out half", "all": "Both halves", GUARD: "Generated-file set"}
    for name, qs in by_split.items():
        s = summary(run, qs)
        f = final["splits"][name]
        same_metrics = s["rows"][APP]["overall"] == f["rows"][APP]["overall"]
        final_ranks = {q["id"]: q["rank"] for q in f.get("queries", [])}
        ranks = {q.id: run.rows[APP][q.id].rank for q in qs}
        same_ranks = bool(final_ranks) and ranks == final_ranks
        data["identical"] &= same_metrics and same_ranks
        d = s["divider"]
        data["splits"][name] = {"divider": d, "same_metrics_as_final": same_metrics, "same_ranks_as_final": same_ranks,
                                "final_divider": f["divider"]}
        by_set = "; ".join(f"{k} {v['median_above']}, {v['all_correct_above']} of {v['of']}" for k, v in d["set"].items())
        md.append(f"| {names.get(name, name)} | {len(qs)} | {'yes' if same_metrics else 'NO'} | "
                  f"{'yes' if same_ranks else 'NO'} | {f['divider']['median_above']} → {d['median_above']} | "
                  f"{f['divider']['all_correct_above']} → {d['all_correct_above']} of {d['of']} ({by_set}) |")
    return "\n".join(md), data


def _models() -> dict[str, str]:
    cfg = Config()
    return {"text": cfg.text_model, "clip": f"{cfg.clip_vision_model}+{cfg.clip_text_model}",
            "ocr": f"{cfg.ocr_det_model}+{cfg.ocr_rec_model}"}


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def write_report(md: str, data: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".md").write_text(md + "\n", encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def log_heldout(paths: Paths, purpose: str, settings: dict, *, when: str | None = None, report: Path | None = None,
                note: str = "") -> int:
    """Append a run that touched the held-out half (`when`: the run's own time, which its report carries too);
    returns how many final runs there have been."""
    paths.out.mkdir(parents=True, exist_ok=True)
    entry = {"when": when or datetime.now().isoformat(timespec="seconds"), "purpose": purpose, "commit": _commit(),
             "settings": settings}
    if report is not None:
        entry["report"] = report.with_suffix(".json").relative_to(paths.repo).as_posix()
    if note:
        entry["note"] = note
    with paths.heldout_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    with paths.heldout_log.open(encoding="utf-8") as f:
        return sum(json.loads(line)["purpose"] == "final" for line in f if line.strip())


# What the runs logged before the log took a note measured (their log lines stay as written).
_EARLY_NOTES = {
    "2026-10-19T13:10:31": "the settings at the start of M6 item 3",
    "2026-10-19T17:04:07": "after fixes x, y, n and f; the divider's first bar (0.31)",
    "2026-10-19T17:39:30": "the divider's bar at 0.50; ranks are the final run's, checked query by query",
    "2026-10-19T18:39:19": "the final settings, with 1,000 generated files added to the index",
}
_SIDE_START, _SIDE_END = "<!-- held-out runs: written by vault eval -->", "<!-- /held-out runs -->"


def heldout_runs(paths: Paths) -> list[str]:
    """Every logged run on the held-out half side by side (SPEC §9), the app's row, from each run's report: the
    one the log names, else the report with the run's time and commit. A divider rerun reports the divider only."""
    reports: dict[tuple[str, str], dict] = {}
    for f in sorted(paths.out.glob("results*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(d, dict) and "heldout" in d.get("splits", {}):
            reports[(d["when"], d["commit"])] = d
    log = [e for e in (json.loads(line) for line in paths.heldout_log.read_text(encoding="utf-8").splitlines()
                       if line.strip()) if e["purpose"] != "coverage"]  # the coverage run touches no held-out query
    md = [_SIDE_START, "## Every run on the held-out half", "",
          f"Each run that touched the held-out half, in the order of `eval/{paths.heldout_log.name}`; the app's row. "
          "Picture: the picture queries, R@1 / R@5 / R@10. Divider: the median number of results above it, and the "
          "queries with every correct file above it.", ""]
    for split, heading in (("heldout", "Held-out half"), ("all", "Both halves"), ("tune", "Tuning half")):
        md += [f"**{heading}**", "", "| # | run | when, commit | files | R@1 | R@5 | R@10 | MRR@10 | picture | "
               "divider (median) | every correct file above it |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for i, e in enumerate(log, start=1):
            d = (json.loads((paths.repo / e["report"]).read_text(encoding="utf-8")) if e.get("report")
                 else reports.get((e["when"], e["commit"])))
            run = f"{e['purpose']}: {e.get('note') or _EARLY_NOTES.get(e['when'], '')}".rstrip(": ")
            when = f"{e['when'][5:16].replace('T', ' ')}, {e['commit']}"
            if d is None or split not in d["splits"]:
                md.append(f"| {i} | {run} | {when} | | no report | | | | | | |")
                continue
            entry = d["splits"][split]
            div = entry["divider"]
            div_cells = f"{div['median_above']} | {div['all_correct_above']} of {div['of']}"
            if "rows" not in entry:  # a divider rerun: the final run's ranking
                final = reports.get((d["final_run"]["when"], d["final_run"]["commit"]), {})
                files = final.get("corpus", {}).get("files", "")
                md.append(f"| {i} | {run} | {when} | {files} | as the final run | | | | | {div_cells} |")
                continue
            app = entry["rows"][APP]
            m, pic = app["overall"], app["search type"].get("picture")
            pic_cell = f"{pic['r1']} / {pic['r5']} / {pic['r10']} of {pic['n']}" if pic else ""
            md.append(f"| {i} | {run} | {when} | {d['corpus']['files']} | {m['r1']} | {m['r5']} | {m['r10']} | "
                      f"{m['mrr']:.3f} | {pic_cell} | {div_cells} |")
        md.append("")
    return md + [_SIDE_END]


def _put_section(target: Path, lines: list[str], start: str, end: str, after: str | None = None) -> None:
    """Write `lines` (which begin with `start` and end with `end`) into `target`: over an earlier copy, else
    right after the `after` marker if it's there, else before the first section."""
    text = target.read_text(encoding="utf-8")
    if start in text:
        head, _, rest = text.partition(start)
        tail = rest.partition(end)[2].lstrip("\n")
    elif after and after in text:
        head, _, tail = text.partition(after)
        head, tail = head + after + "\n\n", tail.lstrip("\n")
    else:
        head, sep, tail = text.partition("\n## ")
        head, tail = head + "\n", (sep.lstrip("\n") + tail) if sep else ""
    target.write_text(head + "\n".join(lines) + "\n\n" + tail, encoding="utf-8")


def put_heldout_runs(paths: Paths) -> Path | None:
    """Write the side-by-side section into the current settings' report (`eval/results.md`) before its first
    section, replacing an earlier copy; nothing if that report or the log doesn't exist yet."""
    target = paths.out / "results.md"
    if not target.is_file() or not paths.heldout_log.is_file():
        return None
    _put_section(target, heldout_runs(paths), _SIDE_START, _SIDE_END)
    return target


# §9's coverage (owner, 20 Oct): the owner's 44 queries cover the four search types the owner uses; the rest of §9
# comes from the generated files, the generated set and the coverage set. Rows: (label, minimum, test on a query).
_COV_START, _COV_END = "<!-- coverage queries: written by vault eval -->", "<!-- /coverage queries -->"
_SECTION9_MIN = {"queries": 40, "exact (exact word)": 6, "semantic (meaning)": 6, "visual (picture)": 6,
                 "filtered (kind words)": 6, "mixed": 6, "a .docx or .pptx correct file": 6,
                 "a tall screenshot correct file": 3, "a PDF correct file with text inside an image on a page": 3}


def section9_counts(lib: Library, paths: Paths) -> dict[str, dict[str, int]]:
    """§9's coverage counted per query set (owner, generated, coverage). A tall screenshot is at least twice as
    high as it is wide; a PDF with text inside an image has a page with both native text and OCR'd text (an upper
    bound for the owner's private books: the query may be about the native text)."""
    ids = private_ids(paths)
    facts: dict[str, dict] = {}
    with lib.connect() as conn:
        pdf_image = {fid for (fid,) in conn.execute(
            "SELECT DISTINCT a.file_id FROM chunks a JOIN chunks b ON a.file_id = b.file_id AND a.page = b.page "
            "WHERE a.source = 'pdf_text' AND b.source = 'ocr'")}
        for path, fid, kind, w, h in conn.execute(
                "SELECT p.path, f.id, f.kind, f.width, f.height FROM paths p JOIN files f ON f.id = p.file_id"):
            facts[key_of(path, paths, ids)] = {"suffix": Path(path).suffix.lower(), "pdf_image": fid in pdf_image,
                                               "tall": kind == "screenshot" and bool(w and h and h >= 2 * w)}

    def has(q: Query, fact: Callable[[dict], bool]) -> bool:
        return any(fact(facts.get(k, {})) for k in q.relevant)

    tests: dict[str, Callable[[Query], bool]] = {
        "queries": lambda q: True,
        "exact (exact word)": lambda q: q.category == "exact word",
        "semantic (meaning)": lambda q: q.category == "meaning",
        "visual (picture)": lambda q: q.category == "picture",
        "filtered (kind words)": lambda q: q.category == "filtered",
        "mixed": lambda q: q.category == "mixed",
        "inside a scan (the owner's type, not in §9)": lambda q: q.category == "inside a scan",
        "a .docx or .pptx correct file": lambda q: has(q, lambda f: f.get("suffix") in (".docx", ".pptx")),
        "a tall screenshot correct file": lambda q: has(q, lambda f: f.get("tall", False)),
        "a PDF correct file with text inside an image on a page": lambda q: has(q, lambda f: f.get("pdf_image", False)),
    }
    sets = {name: load_queries(paths, name) for name in ("owner", "generated", "coverage")}
    return {label: {name: sum(map(test, qs)) for name, qs in sets.items()} for label, test in tests.items()}


def coverage_section(paths: Paths) -> list[str]:
    """The coverage run, reported apart from the owner's queries (`eval/results.md`)."""
    data = json.loads((paths.out / "results_coverage.json").read_text(encoding="utf-8"))
    entry = data["splits"][COVERAGE]
    app = entry["rows"][APP]
    md = [_COV_START, f"## Coverage queries ({entry['rows'][APP]['overall']['n']} queries about the generated files)",
          "",
          f"Run {data['when']} at commit {data['commit']}, logged as `coverage`; full report in "
          "[`results_coverage.md`](results_coverage.md). Written from the generated files' contents before any search, "
          "for the §9 coverage the owner's 44 queries don't aim at: Word and PowerPoint files, kind words (filtered) and "
          "mixed queries (an exact number, code or name with words about the content). Nothing was tuned on them.", ""]
    md += _table("App, overall and by search type", {"all": app["overall"], **app["search type"]}) + [""]
    md += _table("App, by file type of the first correct file", app["file type"]) + [""]
    md += ["| id | search type | query | correct file type | rank |", "|---|---|---|---|---|"]
    for q in entry.get("queries", []):
        md.append(f"| {q['id']} | {q['search_type']} | {q['query']} | {q['file_type']} | {q['rank'] or 'not in 50'} |")
    counts = data.get("section9")
    if counts:
        md += ["", "**§9 coverage, by query set** (the owner's 44 deliberately cover the owner's four search types; the "
               "rest comes from the generated files):", "",
               "| queries with | owner's | generated | coverage | all | §9 asks |", "|---|---|---|---|---|---|"]
        for label, by in counts.items():
            need = _SECTION9_MIN.get(label)
            md.append(f"| {label} | {by['owner']} | {by['generated']} | {by['coverage']} | {sum(by.values())} | "
                      f"{f'≥ {need}' if need else '–'} |")
    return md + [_COV_END]


def put_coverage(paths: Paths) -> Path | None:
    """Write the coverage section into `eval/results.md`, after the held-out runs."""
    target = paths.out / "results.md"
    if not target.is_file() or not (paths.out / "results_coverage.json").is_file():
        return None
    _put_section(target, coverage_section(paths), _COV_START, _COV_END, after=_SIDE_END)
    return target
