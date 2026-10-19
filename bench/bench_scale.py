"""Scale test (open item u): ~1,000 generated files indexed into a scratch index, never the demo index.

    python bench/bench_scale.py make <folder> [--files 1000]      # bench_upload's generator and mix, × files/100
    python bench/bench_scale.py index <folder> --data <dir> [--with-corpus] [--out report.json]

`make` writes the files from fixed seeds; the fonts and image encoders differ by OS, so two machines' files differ
slightly (1,000 files: 1,145.9 MB on Windows, 1,146.8 MB on Linux). `index` indexes them, with `corpus/` too
when asked, into `<dir>` (a fresh data dir), then searches the committed queries (the owner's public ones and the
generated-file set; the private ones never leave the owner's laptop) and prints one JSON report: indexing time per
stage and in total, files/sec per kind, the database and thumbnails on disk, the process's peak RAM, and search
latency p50/p95 (the engine's stage timers, as `vault eval` reports them, and wall time). On the deploy host run it
under `nice -n 19` so the live demo keeps the CPU. Quality at scale is `vault eval --purpose scale --extra <folder>`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

from bench_upload import make_upload  # noqa: E402
from vault.config import Config  # noqa: E402
from vault.eval import Paths, load_queries, percentile  # noqa: E402
from vault.library import Library  # noqa: E402
from vault.search.engine import search  # noqa: E402


def peak_rss_mb() -> float:
    """This process's peak resident memory (Linux, macOS: ru_maxrss; Windows: peak working set)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        c = Counters()
        c.cb = ctypes.sizeof(c)
        current = ctypes.windll.kernel32.GetCurrentProcess
        current.restype = wintypes.HANDLE  # a pseudo-handle (-1): a plain int return would truncate it
        info = ctypes.windll.psapi.GetProcessMemoryInfo
        info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        info(current(), ctypes.byref(c), c.cb)
        return c.PeakWorkingSetSize / 2**20
    import resource

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 2**20 if sys.platform == "darwin" else rss / 1024


def size_mb(path: Path) -> float:
    if path.is_file():
        return path.stat().st_size / 2**20
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 2**20 if path.exists() else 0.0


def cmd_make(args: argparse.Namespace) -> None:
    if args.files < 100 or args.files % 100:
        raise SystemExit("--files must be a multiple of 100")
    t0 = time.perf_counter()
    files = make_upload(args.folder, args.files // 100)
    print(json.dumps({"files": len(files), "megabytes": round(sum(p.stat().st_size for p in files) / 2**20, 1),
                      "seconds": round(time.perf_counter() - t0, 1)}))


def cmd_index(args: argparse.Namespace) -> None:
    if (args.data / "vault.db").exists():
        raise SystemExit(f"{args.data} already holds an index: the scale test starts from an empty one")
    cfg = Config(data_dir=args.data)
    lib = Library(cfg)
    roots = [args.folder] + ([ROOT / "corpus"] if args.with_corpus else [])
    marks: dict[str, float] = {}
    t0 = time.perf_counter()
    rep = lib.index(roots, listed=lambda: marks.setdefault("listed", time.perf_counter() - t0))
    total = time.perf_counter() - t0
    index_peak = peak_rss_mb()
    paths = Paths()
    queries = [q for q in load_queries(paths, "owner") if q.public] + load_queries(paths, "generated")
    engine_ms, wall_ms = [], []
    with lib.connect() as conn:
        n_files = conn.execute("SELECT count(*) FROM files WHERE status = 'ok'").fetchone()[0]
        n_chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        text_index, image_index = lib.vectors(conn)
        search(conn, "warm up", cfg, text_index=text_index, image_index=image_index)  # loads the models
        for _ in range(args.repeat):
            for q in queries:
                t = time.perf_counter()
                resp = search(conn, q.text, cfg, text_index=text_index, image_index=image_index)
                wall_ms.append((time.perf_counter() - t) * 1000)
                engine_ms.append(sum(resp.timings_ms.values()))
    report = {
        "roots": [str(r) for r in roots], "files_indexed": n_files, "chunks": n_chunks,
        "errors": len(rep.errors), "listed_s": round(marks.get("listed", 0.0), 1), "total_s": round(total, 1),
        "stages_s": {k: round(v, 1) for k, v in rep.timings.items()},
        "files_per_sec": {k: round(v, 2) for k, v in rep.files_per_sec().items()},
        "per_kind_files": {k: int(v["files"]) for k, v in rep.per_kind.items()},
        "ocr_images": rep.ocr_images,
        "db_mb": round(sum(size_mb(p) for p in args.data.glob("vault.db*")), 1),
        "thumbs_mb": round(size_mb(args.data / "thumbs"), 1),
        "peak_rss_mb_indexing": round(index_peak, 0), "peak_rss_mb": round(peak_rss_mb(), 0),
        "queries": {"owner_public": sum(q.split != "guard" for q in queries),
                    "generated": sum(q.split == "guard" for q in queries), "repeat": args.repeat},
        "search_ms": {"engine_p50": round(percentile(engine_ms, 0.5), 1),
                      "engine_p95": round(percentile(engine_ms, 0.95), 1),
                      "wall_p50": round(percentile(wall_ms, 0.5), 1), "wall_p95": round(percentile(wall_ms, 0.95), 1)},
    }
    text = json.dumps(report, indent=1)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="generate the files")
    m.add_argument("folder", type=Path)
    m.add_argument("--files", type=int, default=1000)
    m.set_defaults(func=cmd_make)
    i = sub.add_parser("index", help="index them into a fresh data dir and time searches")
    i.add_argument("folder", type=Path)
    i.add_argument("--data", type=Path, required=True)
    i.add_argument("--with-corpus", action="store_true", help="index corpus/ too (the demo's files)")
    i.add_argument("--repeat", type=int, default=3, help="times each query runs (default 3)")
    i.add_argument("--out", type=Path)
    i.set_defaults(func=cmd_index)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
