# Benchmarks

Scripts that reproduce the speed numbers in SPEC §19. Run them from the repo root with the venv active
(`.venv\Scripts\activate` on Windows, `source .venv/bin/activate` on Linux/macOS). Everything they generate
goes to `bench/generated/`, which is gitignored; delete it when you're done.

| Script | What it measures |
|---|---|
| `bench_ocr.py` | OCR time and word recall for one config on 8 generated 1920×1080 UI screenshots |
| `compare_ocr.py` | `bench_ocr.py` for several configs, interleaved, one process per run, with a summary table |
| `make_bench_corpus.py` | Generates the mixed folder used for ingest speed (files/sec per kind) |
| `calibrate.py` | Calibrates the provisional search thresholds on generated data (CLAUDE.md, SPEC §9 tuning rule) |
| `scenes.py` | Drawn scenes, text-free photos and screen captures for the CLIP calibrations |

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
```

Each prints a summary and writes every score to `bench/generated/calibrate_<name>.json`. The texts are written
in `calibrate.py`; the pictures are drawn by `scenes.py`. Both need the models cached (`HF_HUB_OFFLINE=1` works
after the first download).
