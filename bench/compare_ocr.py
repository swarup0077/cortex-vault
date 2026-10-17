"""Interleaved before/after OCR benchmark (SPEC §19): each config in its own process, alternating, so the
machine's drifting speed hits every config alike.

    python bench/compare_ocr.py [--repeats 3] [--only before after]

Appends every run to bench/generated/compare_ocr.jsonl and prints a summary table.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"

CONFIGS: dict[str, dict] = {
    # RapidOCR's own defaults: detector short side ≥ 736, angle classifier on, ONNX Runtime's default threads
    "before": {"ocr_det_limit_type": "min", "ocr_det_limit_side": 736, "ocr_cls_screenshots": True, "ocr_threads": 0},
    "after": {},  # the current Config defaults
    "after-default-threads": {"ocr_threads": 0},
}


def run(name: str) -> dict:
    args = [f"{k}={json.dumps(v)}" for k, v in CONFIGS[name].items()] + [f"tag={name}"]
    out = subprocess.run([sys.executable, str(HERE / "bench_ocr.py"), *args],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out.strip().splitlines()[-1])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--only", nargs="*", choices=list(CONFIGS), default=list(CONFIGS))
    args = ap.parse_args()
    GENERATED.mkdir(exist_ok=True)
    results: dict[str, list[dict]] = {name: [] for name in args.only}
    with open(GENERATED / "compare_ocr.jsonl", "a", encoding="utf-8") as log:
        for i in range(args.repeats):
            for name in args.only:
                r = run(name)
                results[name].append(r)
                log.write(json.dumps(r) + "\n")
                log.flush()
                print(f"[{i + 1}/{args.repeats}] {name}: {r['ms_per_image_median']} ms, recall {r['word_recall']:.2%}",
                      flush=True)

    print("\n| config | runs (ms) | median | detect | recognise | word recall |")
    print("|---|---|---|---|---|---|")
    for name, rs in results.items():
        ms = [r["ms_per_image_median"] for r in rs]
        med = lambda key: round(statistics.median(r[key] for r in rs))  # noqa: E731
        print(f"| {name} | {' / '.join(map(str, ms))} | {statistics.median(ms):.0f} | {med('det_ms')} | "
              f"{med('rec_ms')} | {statistics.mean(r['word_recall'] for r in rs):.1%} |")


if __name__ == "__main__":
    main()
