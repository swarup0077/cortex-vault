"""Tune the provisional values on the eval's tuning half (SPEC §9, open item o; M6 item 3 step 5).

    python bench/tune.py [--only NAME ...]

One parameter at a time, on a small grid around its value, starting from the app's settings and keeping each
accepted change for the next parameter. A value is kept only if, against the settings it would replace:
  - at least 2 more tuning-half queries have a correct file in the top 5;
  - no tuning-half query loses its top-5 place (so no search type loses one);
  - fewer than 2 generated-set queries lose their top-5 place (a guardrail, never a target).
Then the divider's two candidate rules: each bar is raised as far as it goes without pushing below the divider any
correct tuning-half file that is above it without the bar; the generated guardrail applies too. If the candidates
tie on the median count above the divider, the simpler rule (share) wins.

Only the tuning half and the generated set are read; a held-out query never reaches this script. Results go to
bench/generated/tune.json and tune.md (git-ignored) and are copied into SPEC §19 by hand. Query ids only, never a
private query's text.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from vault import eval as ev  # noqa: E402
from vault.config import Config  # noqa: E402
from vault.search.engine import search  # noqa: E402
from vault.search.retrievers import load_text_index  # noqa: E402

OUT = REPO / "bench" / "generated"

# Query-time provisional values (SPEC §19), in open item o's order, then fixes x and n. Not here: snippet_prose_words
# (it picks the page shown, never a file's rank, so the eval can't see it), clip_screenshot_margin and
# pdf_ocr_stop_conf (index-time; item w measures the first).
GRID: dict[str, list] = {
    "clip_min_cosine": [0.22, 0.24, 0.26, 0.28, 0.30],
    "dense_min_cosine": [0.36, 0.39, 0.42, 0.45, 0.48, 0.51],
    "clip_strong_z": [2.5, 2.75, 3.0, 3.25, 3.5, 4.0],
    "clip_strong_cosine": [0.26, 0.27, 0.28, 0.29, 0.30],
    "visual_bm25_factor": [0.25, 0.5, 0.75, 1.0],
    "visual_clip_factor": [1.5, 2.0, 3.0, 4.0],
    "dense_min_word": [2, 3, 4],
    "toc_min_entries": [3, 4, 5, 6],
}
DIVIDER: dict[str, list] = {
    "divider_min_share": [round(0.01 * i, 2) for i in range(1, 96)],  # up to the first value that fails
    "divider_min_terms": [2, 3, 4, 5],
}


@dataclass
class Result:
    rank: dict[str, int | None]  # query id → rank of its first correct file (results, then weaker)
    above: dict[str, int]  # query id → results above the divider
    correct_above: dict[str, frozenset[str]]  # query id → its correct files above the divider


class Bench:
    def __init__(self) -> None:
        self.paths = ev.Paths()
        self.lib = ev.eval_library(Config(), self.paths, index=False)
        self.ids = ev.private_ids(self.paths)
        self.tune = [q for q in ev.load_queries(self.paths, "owner") if q.split == "tune"]
        self.guard = ev.load_queries(self.paths, "generated")
        assert self.tune and all(q.split == "tune" for q in self.tune)
        self._cm = self.lib.connect()
        self.conn = self._cm.__enter__()
        self.text_index, self.image_index = self.lib.vectors(self.conn)
        self._index_for: dict[tuple[int, int], object] = {}

    def key(self, stored: str) -> str:
        return ev.key_of(stored, self.paths, self.ids)

    def run(self, overrides: dict, queries) -> Result:
        cfg = ev.configure(self.lib.cfg, overrides)
        text_index = self.text_index
        sees = (cfg.dense_min_word, cfg.toc_min_entries)
        if sees != (self.lib.cfg.dense_min_word, self.lib.cfg.toc_min_entries):
            if sees not in self._index_for:
                self._index_for[sees] = load_text_index(self.conn, self.text_index.matrix.shape[1], *sees)
            text_index = self._index_for[sees]
        res = Result({}, {}, {})
        for q in queries:
            resp = search(self.conn, q.text, cfg, text_index=text_index, image_index=self.image_index)
            res.rank[q.id] = ev.outcome(resp, q.relevant, self.key).rank
            res.above[q.id] = len(resp.results)
            above_keys = {self.key(p) for r in resp.results for p in r.paths}
            res.correct_above[q.id] = frozenset(k for k in q.relevant if k in above_keys)
        return res


def top5(r: Result) -> set[str]:
    return {q for q, rank in r.rank.items() if rank is not None and rank <= 5}


def scores(r: Result, qs) -> dict:
    m = ev.metrics([ev.Outcome(r.rank[q.id], r.above[q.id], False, {}, 0) for q in qs])
    m["divider_median"] = statistics.median(r.above[q.id] for q in qs)
    m["correct_above"] = sum(len(r.correct_above[q.id]) for q in qs)
    m["by_type_r5"] = {}
    for q in qs:
        m["by_type_r5"].setdefault(q.category, 0)
        m["by_type_r5"][q.category] += r.rank[q.id] is not None and r.rank[q.id] <= 5
    return m


def compare(base_t: Result, t: Result, base_g: Result, g: Result) -> dict:
    gained, lost = sorted(top5(t) - top5(base_t)), sorted(top5(base_t) - top5(t))
    g_lost = sorted(top5(base_g) - top5(g))
    return {"gained": gained, "lost": lost, "guard_lost": g_lost,
            "keep": len(top5(t)) - len(top5(base_t)) >= 2 and not lost and len(g_lost) < 2}


def pushed_below(base: Result, r: Result) -> list[str]:
    """Correct files above the divider without the bar and below it with it, as `query:file` (ids only)."""
    return [f"{q}:{k}" for q in base.correct_above for k in sorted(base.correct_above[q] - r.correct_above[q])]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*", default=None, help="parameters to try (default: all, then the divider)")
    args = ap.parse_args()
    b = Bench()
    log: list[dict] = []
    settings: dict = {}
    t0 = time.perf_counter()
    base_t, base_g = b.run(settings, b.tune), b.run(settings, b.guard)
    start = {"tune": scores(base_t, b.tune), "guard": scores(base_g, b.guard)}
    print(f"start: tune R@5 {start['tune']['r5']}/{len(b.tune)}, guard R@5 {start['guard']['r5']}/{len(b.guard)}, "
          f"divider median {start['tune']['divider_median']} ({time.perf_counter() - t0:.0f} s)")

    for name, grid in GRID.items():
        if args.only is not None and name not in args.only:
            continue
        current = settings.get(name, getattr(Config(), name))
        tried = []
        for value in grid:
            trial = {**settings, name: value}
            t, g = b.run(trial, b.tune), b.run(trial, b.guard)
            c = compare(base_t, t, base_g, g) if value != current else {"gained": [], "lost": [], "guard_lost": [],
                                                                          "keep": False}
            tried.append({"value": value, "current": value == current, "tune": scores(t, b.tune),
                          "guard": scores(g, b.guard), **c, "_t": t, "_g": g})
            s = tried[-1]
            print(f"{name}={value}{' (current)' if value == current else ''}: tune R@1/5/10 "
                  f"{s['tune']['r1']}/{s['tune']['r5']}/{s['tune']['r10']} MRR {s['tune']['mrr']:.3f} "
                  f"+{s['gained']} -{s['lost']}; guard R@5 {s['guard']['r5']} -{s['guard_lost']}; "
                  f"divider {s['tune']['divider_median']}{'  KEEP?' if s['keep'] else ''}")
        kept = [s for s in tried if s["keep"]]
        if kept:  # the most gained, then the value nearest the current one
            best = max(kept, key=lambda s: (len(s["gained"]), -abs(s["value"] - current)))
            settings[name] = best["value"]
            base_t, base_g = best["_t"], best["_g"]
        log.append({"name": name, "from": current, "kept": settings.get(name, current) if kept else None,
                    "tried": [{k: v for k, v in s.items() if not k.startswith("_")} for s in tried]})

    divider: dict[str, dict] = {}
    for name, grid in DIVIDER.items():
        if args.only is not None and name not in args.only:
            continue
        tried, best = [], None
        for value in grid:
            trial = {**settings, name: value}
            t, g = b.run(trial, b.tune), b.run(trial, b.guard)
            c = compare(base_t, t, base_g, g)
            pushed, g_pushed = pushed_below(base_t, t), pushed_below(base_g, g)
            ok = not pushed and not c["lost"] and len(c["guard_lost"]) < 2
            s = {"value": value, "tune": scores(t, b.tune), "guard": scores(g, b.guard), **c, "pushed_below": pushed,
                 "guard_pushed_below": g_pushed, "ok": ok}
            tried.append(s)
            print(f"{name}={value}: divider median {s['tune']['divider_median']} (guard "
                  f"{s['guard']['divider_median']}); pushed below {pushed or '-'}; guard pushed {len(g_pushed)}; "
                  f"top-5 -{c['lost']} guard -{c['guard_lost']}{'  ok' if ok else ''}")
            if ok:
                best = s
            else:
                break  # a higher bar only pushes more below
        divider[name] = {"best": best["value"] if best else None,
                         "median": best["tune"]["divider_median"] if best else None, "tried": tried}

    choice = None
    if divider:
        cands = {n: d for n, d in divider.items() if d["best"] is not None}
        if cands:
            low = min(d["median"] for d in cands.values())
            tied = [n for n, d in cands.items() if d["median"] == low]
            choice = "divider_min_share" if "divider_min_share" in tied else tied[0]
            settings[choice] = cands[choice]["best"]
    final_t, final_g = b.run(settings, b.tune), b.run(settings, b.guard)
    out = {"start": start, "params": log, "divider": divider, "divider_choice": choice, "settings": settings,
           "final": {"tune": scores(final_t, b.tune), "guard": scores(final_g, b.guard)},
           "minutes": round((time.perf_counter() - t0) / 60, 1)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tune.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"settings: {settings or 'unchanged'}; final tune R@1/5/10 {out['final']['tune']['r1']}/"
          f"{out['final']['tune']['r5']}/{out['final']['tune']['r10']}, divider median "
          f"{out['final']['tune']['divider_median']}; wrote {OUT / 'tune.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
