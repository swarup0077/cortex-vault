# Benchmarks

Scripts that reproduce the speed numbers in SPEC §19. Run them from the repo root with the venv active
(`.venv\Scripts\activate` on Windows, `source .venv/bin/activate` on Linux/macOS). Everything they generate
goes to `bench/generated/`, which is gitignored; delete it when you're done.

| Script | What it measures |
|---|---|
| `bench_query.py` | Search latency per stage (p50 / p95) from the engine's stage timers, on the demo index or another |
| `bench_embed.py` | Text-embedding chunks/sec for one config (128 ~700-character chunks of SPEC.md, median of 3) |
| `bench_ocr.py` | OCR time and word recall for one config on 8 generated 1920×1080 UI screenshots |
| `compare_ocr.py` | `bench_ocr.py` for several configs, interleaved, one process per run, with a summary table |
| `make_bench_corpus.py` | Generates the mixed folder used for ingest speed (files/sec per kind) |
| `calibrate.py` | Calibrates the provisional search thresholds on generated data (CLAUDE.md, SPEC §9 tuning rule) |
| `scenes.py` | Drawn scenes, text-free photos and screen captures for the CLIP calibrations |
| `tune.py` | M6 tuning (SPEC §9, §19): each provisional value on a small grid, then the divider's two candidate bars, on the eval's tuning half with the generated set as a guardrail; never reads the held-out half |

The OCR models ship inside the `rapidocr` wheel. Only the ingest run needs bge-small, which downloads once
from the HF Hub; after that, `HF_HUB_OFFLINE=1` works.

Laptop speed drifts 10–20% between runs (power and thermal state). Compare configs only in interleaved runs,
on AC power, with nothing else running.

## OCR: before/after (§19 "OCR benchmark")

```
python bench/compare_ocr.py
```

This runs `before` (RapidOCR's defaults), `after` (the current `Config` defaults) and `after-default-threads`,
alternating, 3 times each (about 15 minutes on the dev laptop). It prints the table and appends every run to
`bench/generated/compare_ocr.jsonl`.

## OCR: single configs (§19 sweeps)

Arguments are `Config` fields as `key=value`, plus `n_images`, `rounds`, `workers` and `tag`:

```
python bench/bench_ocr.py                                    # current defaults
python bench/bench_ocr.py ocr_rec_batch=12                   # recognition batch
python bench/bench_ocr.py ocr_cls_screenshots=true           # angle classifier on
python bench/bench_ocr.py ocr_det_limit_side=736             # detector long side
python bench/bench_ocr.py ocr_det_limit_type=min ocr_det_limit_side=736   # RapidOCR's detector default
python bench/bench_ocr.py ocr_threads=4                      # 0 = ONNX Runtime's own default
python bench/bench_ocr.py workers=2 ocr_threads=4            # two images OCR'd in parallel (throughput)
```

Each prints one JSON line: median ms per image, the detect/classify/recognise split (single worker only),
word recall, and the thread count actually used.

## Embedding (§19 "Embedding benchmark")

```
python bench/bench_embed.py                      # the app's defaults: quantized bge-small, default threads, batch 32
python bench/bench_embed.py threads=4 batch=8    # threads (0 = ONNX Runtime's default) and batch size
```

Each prints one JSON line with the median chunks/sec of 3 passes after a warm-up batch. Run each config in
its own process, interleaved with the others.

## Search latency (§7.8, §8)

```
python bench/bench_query.py                          # the committed demo index, 5 rounds of 16 queries
python bench/bench_query.py data_dir=data rounds=10  # your own index
```

Prints the total and each stage's p50 / p95 (ms), after a warm-up pass. The UI's search-as-you-type delay
(300 ms) goes up if a search takes more than about 0.5 s on the deploy host.

## Ingest speed (§19 "Measured ingest speed")

Generate the folder (deterministic, about 117 MB):

```
python bench/make_bench_corpus.py
```

Index it with the CLI into a fresh, throwaway data dir. Use a new dir for every run; reusing one makes the
second run all fast path.

```bash
# bash (Linux VM, macOS)
VAULT_DATA_DIR=bench/generated/data1 python -m vault index bench/generated/corpus
```

```powershell
# PowerShell (Windows)
$env:VAULT_DATA_DIR = "bench\generated\data1"; python -m vault index bench\generated\corpus; Remove-Item Env:VAULT_DATA_DIR
```

The report's `files/sec` rows and stage times are the numbers recorded in §19. Run it twice (`data1`, `data2`)
and record both.

## Calibration (§19 provisional values)

No threshold or weight is chosen from anyone's personal files. Each one is calibrated here on generated data,
marked provisional in `vault/config.py`, and tuned in M6 on the eval's `tune` half (SPEC §9).

```
python bench/calibrate.py dense    # dense_min_cosine: bge cosines, matching vs unrelated pairs
python bench/calibrate.py clip     # CLIP's top hit (z and cosine): drawn scenes vs queries no picture answers
python bench/calibrate.py rule     # clip_strong_z / clip_strong_cosine end to end: scene vs paraphrase recall
python bench/calibrate.py kind     # clip_screenshot_margin: generated screen captures vs photo-like pictures
```

`rule` indexes a generated library (the 24 paraphrase texts, 12 texts that share one word with a scene query,
the 12 scenes and 24 or 188 distractor images, no OCR) into `bench/generated/rule_*` with the real models, then
sweeps the strong levels. It takes a few minutes on the dev laptop.

Each prints a summary and writes every score to `bench/generated/calibrate_<name>.json`. The texts are written
in `calibrate.py`; the pictures are drawn by `scenes.py`. Both need the models cached (`HF_HUB_OFFLINE=1` works
after the first download).
