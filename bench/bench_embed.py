"""Text-embedding speed (SPEC §19 "Embedding benchmark"): chunks/sec for one configuration.

128 chunks of ~700 characters cut from SPEC.md on word boundaries, model cached, one warm-up batch, then the
median of 3 timed passes. Run one configuration per process, interleaved with the others, nothing else running.

    python bench/bench_embed.py                       # the defaults the app uses (quantized bge-small, batch 32)
    python bench/bench_embed.py threads=4 batch=8     # ONNX Runtime threads (0 = its default), batch size
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastembed import TextEmbedding  # noqa: E402

from vault.config import Config  # noqa: E402


def chunks(n: int = 128, size: int = 700) -> list[str]:
    words = (ROOT / "SPEC.md").read_text(encoding="utf-8").split()
    out, cur = [], []
    for w in words:
        cur.append(w)
        if sum(len(x) + 1 for x in cur) >= size:
            out.append(" ".join(cur))
            cur = []
            if len(out) == n:
                break
    return out


def main(argv: list[str]) -> None:
    opts = dict(a.split("=", 1) for a in argv)
    threads = int(opts.get("threads", 0)) or None
    batch = int(opts.get("batch", 32))
    cfg = Config()
    model = TextEmbedding(opts.get("model", cfg.text_model), cache_dir=str(cfg.models_dir), threads=threads)
    texts = chunks()
    list(model.embed(texts[:batch], batch_size=batch))  # warm-up
    times = []
    for _ in range(int(opts.get("rounds", 3))):
        t = time.perf_counter()
        list(model.embed(texts, batch_size=batch))
        times.append(time.perf_counter() - t)
    print(json.dumps({"model": opts.get("model", cfg.text_model), "threads": threads or "default", "batch": batch,
                      "chunks": len(texts), "chunks_per_sec": round(len(texts) / statistics.median(times), 1),
                      "runs_s": [round(t, 2) for t in times], "cpus": os.cpu_count(), "tag": opts.get("tag", "")}))


if __name__ == "__main__":
    main(sys.argv[1:])
