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
id only. Per-query detail for the held-out half is written only by the final run, and every run that touches that
half is logged in `eval/heldout_runs.jsonl`.
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

SEED = 20261019
SOURCES = ("owner", "owner-extra")  # queries.csv, then queries_extra.csv: their ids start with q and x
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


def import_queries(csvs: Sequence[Path], paths: Paths = Paths(), seed: int = SEED) -> ImportReport:
    """The owner's CSVs (query, expected_file, search_type; several files separated by "|") → queries with keys.
    The first CSV is `owner` (ids q01…), the second `owner-extra` (x01…). The CSVs are read, never changed."""
    ids = private_ids(paths)
    public = {p.name: p.relative_to(paths.repo).as_posix() for p in paths.corpus.rglob("*") if p.is_file()}
    report = ImportReport([])
    for source, csv_path in zip(SOURCES, csvs):
        prefix = "q" if source == "owner" else "x"
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


def load_queries(paths: Paths = Paths()) -> list[Query]:
    """The full set when this machine has it, else the committed public queries."""
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


def eval_library(cfg: Config, paths: Paths = Paths(), *, index: bool = True) -> Library:
    """corpus/ plus the private files (when this machine has them), indexed incrementally into data/eval_index/."""
    lib = Library(replace(cfg, data_dir=paths.index))
    if index:
        roots = [paths.corpus] + ([paths.private_files] if paths.private_files.is_dir() else [])
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
        "divider": {"median_above": statistics.median([o.above for o in app]) if app else 0,
                    "all_correct_above": sum(o.all_above for o in app), "of": len(app)},
        "latency_ms": {"p50": percentile([o.ms for o in app], 0.5), "p95": percentile([o.ms for o in app], 0.95)},
    }


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


def report(run: Run, *, splits: Sequence[str], detail: Sequence[str], title: str, notes: Sequence[str] = ()) -> tuple[
        str, dict]:
    """Markdown and JSON for `splits` (each, and "all" when there are several); per-query detail only for the
    halves in `detail`."""
    by_split = {s: [q for q in run.queries if q.split == s] for s in splits}
    if len(splits) > 1:
        by_split["all"] = [q for q in run.queries if q.split in splits]
    data = {"title": title, "when": run.when, "corpus": run.corpus, "settings": run.settings,
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
        md += [f"## {'Tuning half' if name == 'tune' else 'Held-out half' if name == 'heldout' else 'Both halves'}"
               f" ({len(qs)} queries)", ""]
        md += _table("Setting", {row: v["overall"] for row, v in s["rows"].items()}) + [""]
        for by in ("search type", "file type", "set"):
            md += _table(f"App, by {by}", app[by]) + [""]
        d = s["divider"]
        md += [f"Weaker-matches divider (app): median {d['median_above']} results above it; every correct file above "
               f"it for {d['all_correct_above']} of {d['of']} queries. Latency: p50 {s['latency_ms']['p50']:.0f} ms, "
               f"p95 {s['latency_ms']['p95']:.0f} ms (engine stage timers).", ""]
        if "queries" in entry:
            misses = [q for q in entry["queries"] if q["rank"] != 1]
            md += [f"Queries without a correct file at #1 ({len(misses)}):", "",
                   "| id | set | search type | query | rank | found by (app) | single-method rank |",
                   "|---|---|---|---|---|---|---|"]
            for q in misses:
                found = ", ".join(f"{m} #{r}" for m, r in q["found_by"].items()) or "–"
                single = ", ".join(f"{m} {r if r else '–'}" for m, r in q["single_method_rank"].items())
                md.append(f"| {q['id']} | {q['set']} | {q['search_type']} | {q['query'] or '(private)'} | "
                          f"{q['rank'] or 'not in 50'} | {found} | {single} |")
            md.append("")
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


def log_heldout(paths: Paths, purpose: str, settings: dict) -> int:
    """Append a run that touched the held-out half; returns how many final runs there have been."""
    paths.out.mkdir(parents=True, exist_ok=True)
    with paths.heldout_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"when": datetime.now().isoformat(timespec="seconds"), "purpose": purpose,
                            "commit": _commit(), "settings": settings}) + "\n")
    with paths.heldout_log.open(encoding="utf-8") as f:
        return sum(json.loads(line)["purpose"] == "final" for line in f if line.strip())
