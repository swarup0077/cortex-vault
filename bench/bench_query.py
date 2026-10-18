"""Query latency from the engine's stage timers (SPEC §7.8, §8): p50 / p95 per stage and in total.

Searches a fixed list of queries against an index (the committed demo index by default, read-only) with the
vectors cached, as the app does, after one warm-up pass. Run it with nothing else busy.

    python bench/bench_query.py                          # demo_index/, 5 rounds
    python bench/bench_query.py data_dir=data rounds=10  # another index
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vault.config import Config  # noqa: E402
from vault.library import Library  # noqa: E402

QUERIES = ["error 0x80070005", "python traceback KeyError", "paper about denoising diffusion models",
           "where is singular value decomposition defined", "slide about gradient descent", "sunset at the beach",
           "whiteboard with equations", "dice on a table", "train ticket PNR", "how much was the electricity bill",
           "lab moved to another room", "bayes theorem medical test example", "max pooling keeps the largest value",
           "notes about git", "photos of mountains", "ab"]


def pct(values: list[float], q: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, round(q * (len(values) - 1)))]


def main(argv: list[str]) -> None:
    opts = dict(a.split("=", 1) for a in argv)
    data_dir = Path(opts.get("data_dir", ROOT / "demo_index"))
    lib = Library(Config(data_dir=data_dir), read_only=data_dir.name == "demo_index", base_dir=ROOT)
    for q in QUERIES:  # warm-up: model loads and vector caches
        lib.search(q)
    stages: dict[str, list[float]] = {}
    totals, walls = [], []
    for _ in range(int(opts.get("rounds", 5))):
        for q in QUERIES:
            t = time.perf_counter()
            resp = lib.search(q)
            walls.append((time.perf_counter() - t) * 1000)
            totals.append(sum(resp.timings_ms.values()))
            for stage, ms in resp.timings_ms.items():
                stages.setdefault(stage, []).append(ms)
    report = {"index": str(data_dir), "queries": len(totals),
              "total_ms": {"p50": round(statistics.median(totals), 1), "p95": round(pct(totals, 0.95), 1)},
              "wall_ms": {"p50": round(statistics.median(walls), 1), "p95": round(pct(walls, 0.95), 1)},
              "stages_ms_p50": {s: round(statistics.median(v), 1) for s, v in stages.items()},
              "stages_ms_p95": {s: round(pct(v, 0.95), 1) for s, v in stages.items()}}
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
