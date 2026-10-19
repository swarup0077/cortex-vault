# Cortex Vault (Personal Search Vault)

Search your own screenshots, photos, PDFs, Word and PowerPoint files in plain language, on your own computer. Cortex
Vault reads the text in every file (with OCR for images and scanned pages), understands what a sentence means, and
looks at what is in each picture, using three small models that run on the CPU. Nothing is sent to a cloud service.

**Live demo:** https://swarup-vault.indiasouthcentral.cloudapp.azure.com/ searches 71 sample files: 52 generated
ones and 19 of the author's own. You can add your own files to search them alongside; they stay on the demo server,
are kept only for your session and are deleted 60 minutes after your last search or upload.

**Source:** https://github.com/swarup0077/cortex-vault (AGPL-3.0; the demo's footer links here).

| Results, with the matched text highlighted in the preview | A picture found by what it shows |
|---|---|
| ![Search results for "error 0x80070005"](design/v0-brass/port/readme-desktop-light-results.png) | ![A picture match](design/v0-brass/port/readme-desktop-light-picture-match.png) |
| **Uploading your own files: searchable by name at once, then by content** | **On a phone, dark theme** |
| ![The upload progress bar](design/v0-brass/port/readme-desktop-bar-reading.png) | <img src="design/v0-brass/port/readme-phone-dark-results.png" alt="Results on a phone" width="180"> |

## How search works

Each query runs through four ranked lists of files, and Reciprocal Rank Fusion merges them:

- **Exact words:** BM25 over SQLite FTS5 (Porter stemming); stopwords are dropped when a content word remains.
- **Every word:** the same BM25, restricted to passages that hold every content word of the query, so a file with
  the whole query in one place beats a file that repeats one word.
- **Meaning:** cosine similarity between the query and each passage, with
  [bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5). Passages below a similarity floor (0.42) are
  left out.
- **Picture:** CLIP ViT-B/32 compares the query with every image. A gate keeps only the clear matches: images scoring
  at least 2 standard deviations above the mean of all candidates (or, with fewer than 20 images, the top 5 above a
  floor). When no file holds every word and CLIP's best image is a strong outlier, well ahead of the next image too,
  the *visual-query rule* gives that image extra weight: "dice on a table" puts the photo of dice first, though no
  text in it says so.

Each list holds up to 100 files, ranked by their best passage, so one long book can't fill a list. Fusion adds
1 / (60 + rank) from each list a file appears in. Results that score under half of the top result's score go below
a **Weaker matches** divider, followed by the files that only the meaning floor or the picture gate kept out, so the
list is never cut short. Each result shows the passage or page that best matches, with the query words marked, and,
for screenshots and scans, boxes around the matching words in the preview.

```mermaid
flowchart LR
    Q["Query"] --> P["Parse<br/>(kind words: screenshot, pdf…)"]
    P --> B["Exact words<br/>BM25, SQLite FTS5"]
    P --> A["Every word<br/>BM25, all terms in one passage"]
    P --> D["Meaning<br/>bge-small, cosine ≥ 0.42"]
    P --> C["Picture<br/>CLIP ViT-B/32 + gate"]
    B --> F["Fuse by file<br/>RRF, k = 60"]
    A --> F
    D --> F
    C --> F
    F --> V{"Score ≥ 50%<br/>of the top?"}
    V -- yes --> R["Results<br/>snippet + highlight boxes"]
    V -- no --> W["Weaker matches"]
```

Indexing reads each file once: names and native text (PDF text layers, Word, PowerPoint, text files) are searchable
within seconds; OCR (RapidOCR, PP-OCRv6), meaning vectors and picture vectors follow in a second pass. Files are
identified by content (SHA-256), so renamed, moved and duplicate files aren't read twice.

## Results

### Retrieval quality

44 queries written by the author before any search, over 95 files: the 52 generated files, 19 of the author's own
public files and 24 private ones (kept out of this repository; queries about them are reported by id only).
A correct file at rank 1, in the top 5 and in the top 10 (Recall@k), and MRR@10. The queries were split in half
with a fixed seed: the tuning half was used to choose settings, and the held-out half was run only in logged runs:
at the start, with the final settings, once more for the divider's numbers after its rule changed, and after the fix
to the visual-query rule (see Known limitations), on the 95 files and on 1,095. [`eval/results.md`](eval/results.md)
holds the current settings' report, with every held-out run side by side. The current settings:

| Queries | n | R@1 | R@5 | R@10 | MRR@10 |
|---|---|---|---|---|---|
| All | 44 | 23 (52%) | 30 (68%) | 35 (80%) | 0.601 |
| Held-out half | 22 | 6 (27%) | 12 (54%) | 16 (73%) | 0.402 |
| Tuning half | 22 | 17 (77%) | 18 (82%) | 19 (86%) | 0.801 |
| Public files | 13 | 5 (38%) | 5 (38%) | 7 (54%) | 0.402 |
| Private files | 31 | 18 (58%) | 25 (81%) | 28 (90%) | 0.685 |
| Exact word | 9 | 7 (78%) | 9 (100%) | 9 (100%) | 0.856 |
| Meaning | 19 | 10 (53%) | 13 (68%) | 15 (79%) | 0.601 |
| Picture | 10 | 3 (30%) | 4 (40%) | 6 (60%) | 0.371 |
| Inside a scan | 6 | 3 (50%) | 4 (67%) | 5 (83%) | 0.604 |

Before that fix, on the same 95 files: 24 / 32 / 36, MRR 0.627, and pictures 5 / 6 / 7 of 10
([`eval/results_final.md`](eval/results_final.md)). Here the fix costs two picture queries and lifts one query about
text in a scan to #1; it pays off in a larger library (Scale test, below).

What each list adds (all 44 queries, R@5 / R@10): exact and every word alone 24 / 25, meaning alone 27 / 34,
picture alone 22 / 29, words + meaning 25 / 31, all three with the picture gate (the app) 30 / 35. Without the gate
the picture list adds more noise than it finds: 30 / 34, and 19 at R@1 against the app's 23. A separate set of 32
queries about the generated files, used only as a guardrail while tuning: 28 (88%) / 32 (100%) / 32 (100%), MRR
0.924.

The held-out half is the honest number, and it is lower: 4 of its 6 queries without a correct file in the top 10
describe abstract plots (fractals, complex-function plots) whose only text is axis numbers (see Known limitations).

### Scale test

1,000 generated files in [`bench/bench_upload.py`](bench/bench_upload.py)'s mix (300 desktop and 100 phone
screenshots, 250 camera-size photos, 100 PDFs with a text layer, 30 scanned and 20 slide-style PDFs, 80 Word, 60
PowerPoint and 60 text files; 1,146.8 MB), indexed with the demo's 71 files on the demo server under `nice -n 19`,
with the live demo running ([`bench/bench_scale.py`](bench/bench_scale.py),
[`eval/results_scale_vm.json`](eval/results_scale_vm.json)):

| Measure | Demo server, 1,071 files |
|---|---|
| Searchable by name and native text | 86 s |
| Fully read | 2,059 s (34.3 min), 0 errors, 6,914 passages |
| Time by stage | OCR 1,622 s (881 images and pages), meaning vectors 216 s, text extraction 148 s, CLIP 75 s, database writes 37 s, thumbnails 15 s |
| On disk | database 25.4 MB, thumbnails 6.0 MB |
| Peak RAM of the indexing process | 2,318 MB |
| Search, 45 public queries × 3 | p50 136 ms, p95 175 ms; after the visual-query fix, p50 134 ms, p95 177 ms |

While it indexed, the live demo answered in 0.07–0.11 s (0.03–0.04 s idle).

**Quality at scale.** The same 1,000 files mixed into the 95 evaluation files, all queries run again on the laptop
with the current settings ([`eval/results_scale.md`](eval/results_scale.md)); nothing was tuned on it:

| Queries | R@1 | R@5 | R@10 | MRR@10 | Results above the divider (median) |
|---|---|---|---|---|---|
| All 44, 95 files → 1,095 files | 23 → 25 | 30 → 29 | 35 → 33 | 0.601 → 0.610 | 19.5 → 19 |
| Held-out half (22) | 6 → 9 | 12 → 12 | 16 → 14 | 0.402 → 0.466 | 19.5 → 21.5 |
| Generated-file set (32) | 28 → 26 | 32 → 31 | 32 → 31 | 0.924 → 0.875 | 10 → 4.5 |

The top of the list holds at this size; two queries leave the top 10. Before the fix to the visual-query rule it
didn't hold: all 44, R@1 24 → 17 and MRR 0.627 → 0.503
([`eval/results_scale_before_z.md`](eval/results_scale_before_z.md)). With 713 images instead of 63, CLIP's best
image passed the rule's outlier test far more often, so the rule fired on text queries and lifted the evaluation's
own screenshots above the correct files (on 12 of the 17 tuning-half queries that aren't picture queries). The rule
now also asks for a clear lead over the next image, and fires on none of them.

### Speed

Search time is the sum of the search engine's stage timers (parse, the four lists, fusion, snippets), the number the
UI shows as "in 0.04 s".

| Where | Index | Queries | p50 | p95 |
|---|---|---|---|---|
| Laptop (Windows, CPU only) | the 95 evaluation files, including a 4,000-passage textbook | the 44 queries | 221 ms | 399 ms |
| Laptop (Windows, CPU only) | the same plus the 1,000 generated files | the 44 queries | 282 ms | 496 ms |
| Demo server (4 vCPUs) | the 71 demo files plus the 1,000 generated files | 45 public queries, 3 times each | 134 ms | 177 ms |

Laptop timings drift between runs (power and thermal state): the evaluation run before the visual-query fix measured
188 / 258 ms on the same 95 files, and timed side by side in one process the fix doesn't slow search down. The demo
server's timings are steadier: 136 / 175 ms before the fix.

The live demo answered "error 0x80070005", "Mandelbrot fractal" and "complex plane with real and imaginary axes" in
0.03–0.04 s. Uploads on the demo server (an Azure D4as_v4: 4 vCPUs, 16 GB RAM), timed with
[`bench/bench_upload.py`](bench/bench_upload.py) from the moment the files reach the server:

| Upload | Size | Searchable by name and native text | Fully read (OCR, meaning, pictures) |
|---|---|---|---|
| 100 mixed files | 115 MB | 8.0 s | 208 s |
| 200 mixed files | 229.5 MB | 16.9 s | 413 s (6.9 min) |

Indexing speed by file type, on the demo server during the scale test (files per second; PDFs include scanned
ones, which are OCR'd page by page): text and Word 5.2, PowerPoint 8.8, PDF 0.20, photo 1.09, screenshot 0.48.

## Privacy

- **Local models.** Text, meaning and picture models run on the CPU with ONNX Runtime. The only network access is the
  first-run download of the models from the Hugging Face Hub; after that it works offline (`HF_HUB_OFFLINE=1`).
- **No cloud calls.** No file, text or query is sent to any service. The app doesn't log queries, in either mode;
  recent searches are kept in your browser only.
- **The demo's rules:** at most 5 upload sessions at once; per session 200 files, 500 MB in total and 50 MB per file;
  uploads are deleted 60 minutes after the session's last search or upload. Files are served by database id or
  session id only, never by a path a visitor supplies. A session is created only by an upload, never by visiting or
  searching. The demo's 19 real files were checked for personal data before they were added, and images carrying GPS
  data are refused when the demo index is built.

## Known limitations

- **Messy handwriting.** The OCR models read print. On generated pages they read printed text with a mean confidence
  of 0.87–0.99 and handwriting-like scribbles with 0.50–0.58; a PDF whose first 3 pages with ink read below 0.72
  stops being OCR'd, so a handwritten notebook is found mostly by its name and what OCR made of its first pages.
- **Abstract plots.** Queries that describe a mathematical plot in words ("a dark central shape surrounded by
  intricate purple branching boundaries") are the weakest group: none of the 5 public held-out queries of this kind has its
  file in the top 5 (ranks 9, 12, 14, 21 and not found). Such images have little text beyond axis numbers, and CLIP
  ranks them only moderately.
- **The picture rule is cautious.** The visual-query rule gives a picture extra weight only when CLIP ranks it well
  ahead of every other image. That stopped it firing on text queries in a larger library (with 1,000 more files it
  had fired on 12 of the 17 tuning-half queries that aren't picture queries; now on none), but it now rarely fires
  for real photos and screenshots: on the author's files, for none of the tuning half's picture queries, and two
  pictures that CLIP alone ranks first (one in each half) dropped to #10 and #11. Drawn test scenes lead the next
  image by 0.039 or more; the real picture the rule used to fire for in the tuning half leads by 0.029, under the
  0.035 required. A picture with
  a near-copy in the library (a forwarded copy, a second shot) never gets the extra weight, because the copy is the
  next image.
- **No date queries.** "Screenshots from last week" searches for those words; it doesn't filter by date. Kind words
  (screenshot, photo, pdf, slides, notes) do filter.
- **Supported files:** PNG, JPEG and WebP images (HEIC with the optional `pillow-heif`), PDF, `.docx`, `.pptx`,
  `.txt` and `.md`. No video, audio, email, `.doc`, `.ppt` or `.xlsx`, and the demo doesn't accept `.zip` uploads.
- **Screenshot or photo.** A file named like a screenshot, or a photo with camera EXIF, is classified by that; other
  images are classified by CLIP, which on the author's real screenshots (plots and textbook pages, not app screens)
  doesn't recognise them as screenshots on its own (0 of 12).

## Run it locally (Windows)

Python 3.12, CPU only. `py` is the launcher the python.org installer adds; with [uv](https://docs.astral.sh/uv/)
instead, make the environment with `uv venv --python 3.12 --seed .venv`.

```bat
git clone https://github.com/swarup0077/cortex-vault
cd cortex-vault
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python -m vault index "C:\Users\you\Pictures\Screenshots" "C:\Users\you\Documents"
python -m vault search "error 0x80070005"
python app.py
```

`python app.py` serves the UI at http://127.0.0.1:7860. Indexing is incremental: run `index` again to pick up new,
changed, moved or deleted files. The first run downloads the models (about 650 MB) into `data/models/`; the index
lives in `data/`. It also runs on Linux, as the demo server does (`source .venv/bin/activate`).

Tests:

```bat
pytest -q                 :: everything
pytest -q -m "not slow"   :: skip the tests that load real models
```

Evaluation and benchmarks: `python -m vault eval` (needs the private files for the full set; the public queries are
in `eval/`), and the scripts in [`bench/`](bench/README.md).

## Models and libraries

| Model | Used for | Licence (source) |
|---|---|---|
| [Qdrant/bge-small-en-v1.5-onnx-Q](https://huggingface.co/Qdrant/bge-small-en-v1.5-onnx-Q), a quantised ONNX port of [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5) | Meaning (384-d text vectors) | MIT (both model cards) |
| [Qdrant/clip-ViT-B-32-vision](https://huggingface.co/Qdrant/clip-ViT-B-32-vision) and [-text](https://huggingface.co/Qdrant/clip-ViT-B-32-text), ONNX ports of [sentence-transformers/clip-ViT-B-32](https://huggingface.co/sentence-transformers/clip-ViT-B-32) | Picture search, screenshot or photo | MIT (Qdrant's model cards; the sentence-transformers card states none) |
| PP-OCRv6 det small, PP-OCRv6 rec small, ch_ppocr_mobile_v2.0_cls, shipped in the `rapidocr` wheel | OCR | Apache-2.0 (RapidOCR's `MODEL_LICENSES.md`; weights from PaddleOCR, Baidu) |

The libraries in `requirements.txt`:

| Library | Version | Licence (package metadata) |
|---|---|---|
| fastembed | 0.9.0 | Apache-2.0 |
| onnxruntime | 1.31.0 | MIT |
| rapidocr | 3.10.0 | Apache-2.0 |
| PyMuPDF | 1.28.2 | AGPL-3.0 or Artifex commercial licence |
| python-docx | 1.2.0 | MIT |
| python-pptx | 1.0.2 | MIT |
| Pillow | 12.3.0 | MIT-CMU |
| NumPy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| Gradio | 6.30.0 | Apache-2.0 |
| FastAPI | 0.143.2 | MIT |
| Uvicorn | 0.54.0 | BSD-3-Clause |
| Rich | 15.0.0 | MIT |
| pytest | 9.1.1 | MIT |

PyMuPDF is used under the AGPL-3.0, whose section 13 asks a network service built on it to offer its users the
source code. Cortex Vault itself is licensed under the AGPL-3.0 to match ([LICENSE](LICENSE)), and the demo's footer
links to this repository: https://github.com/swarup0077/cortex-vault.

## How it was built

Cortex Vault was built between 16 and 19 October 2026 from [SPEC.md](SPEC.md), the single source of truth, one
milestone per session,
with [Claude Code](https://claude.com/claude-code): M0 scaffold and deploy probe, M1 text pipeline, M2 OCR, M3
photos, CLIP and metadata, M4 UI and first deploy, M5 speed and visual polish, M6 real files and the evaluation.
Each change came with a test on generated files (513 tests today), and every threshold was first calibrated on
generated data with a script in [`bench/`](bench/README.md), marked provisional, and tuned only on the tuning half
of the evaluation queries. The decisions, with what was measured and why, are logged in SPEC §19.
