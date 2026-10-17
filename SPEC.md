# Personal Search Vault — Build Spec (v2)

**One line:** search your own photos, screenshots, PDFs and Office files in plain language ("screenshot of the access-denied error", "page where SVD is defined", "slide about gradient descent", "sunset at the beach") and get back the exact file and page or slide, with the matching text highlighted. Everything runs locally; no file leaves the machine.

| | |
|---|---|
| Owner | Swarup |
| Built with | Claude Code (session transcripts are a submission deliverable) |
| Hard deadline | Deployed URL + transcript HTML on MS Teams before **27 Oct 2026, 2 PM** |
| Machine | Windows laptop, CPU only, `C:\projects\personal-search-vault`. Code must also run on Linux (the hosted demo) and macOS (other people's laptops). |
| Python | **3.12** (Ubuntu 24.04 on the Azure VM ships 3.12; RapidOCR's docs list support below 3.13) |
| Version | v2, 16 Oct 2026. Changes since v1 are listed in §20. |

---

## 1. Goals and non-goals

**Goals**
1. Plain-language search across five kinds of file: PDFs, documents (`.docx .txt .md`), slide decks (`.pptx`), screenshots and photos.
2. Hybrid retrieval: exact keywords (BM25), text meaning (dense embeddings) and visual content (CLIP), fused into one ranking.
3. Fully local inference on CPU with open ONNX models. No cloud APIs.
4. Show *where* the match is: the page or slide number, highlighted text, and for images and PDF pages, a box drawn over the matching lines.
5. Two run modes from one codebase:
   - **local**: your real folders, served on `127.0.0.1`;
   - **demo**: hosted, with a read-only, non-personal sample corpus plus temporary per-session uploads.
6. A measured eval harness with an ablation table. This is the centrepiece of the README and of any interview discussion.

**Non-goals**
- No LLM, chat or generated answers.
- No face recognition.
- No handwriting recognition, and no non-English text (the models are English-only).
- No cloud sync, accounts or multi-user state. No mobile app.
- No legacy binary Office formats (`.doc`, `.ppt`), no spreadsheets (`.xlsx`), no video, audio, email or archives.

---

## 2. Run modes

| | `VAULT_MODE=local` (default) | `VAULT_MODE=demo` |
|---|---|---|
| Corpus | Folders the user adds | Prebuilt index of `corpus/` (committed under `demo_index/`) |
| Writes | Index and re-index | Never to the demo index; uploads go into a per-session copy (§8) |
| Bind | `127.0.0.1` only | `127.0.0.1:7860` behind Caddy, which serves HTTPS on 443 (§15) |
| Extras | Index tab; "Open file" button (`vault/platform.py`: `os.startfile` on Windows, `open` on macOS, `xdg-open` on Linux) | Upload tab (files or a whole folder); "How it works" tab |
| File access | Only paths stored in the DB | Only by DB id or session id. A user-supplied path is never read. |
| Network | None after the first model download (`HF_HUB_OFFLINE=1` supported) | Same. The server itself is not private (§16). |

---

## 3. Architecture

```
INGEST  (vault index <folders>)
 folders ─▶ discover ──────▶ classify ─▶ extract ───────────────▶ chunk ──────────▶ embed ────────▶ SQLite
            walk; size+mtime  pdf | doc | PDF text + OCR of scanned   page / slide /    bge-small for    files, paths,
            fast path;        slides |    pages and large images;     paragraph windows; text chunks;   chunks, FTS5,
            sha256; newest    screenshot | docx/pptx/txt/md text;     OCR chunks built  CLIP-vision for  ocr_lines,
            first             photo       image OCR (tiled, reading   from layout       images           vectors,
                                          order, line boxes); dates;  blocks                             thumbs/
                                          thumbnails

SEARCH
 query ─▶ parse ─▶ ┬─ BM25 (FTS5) over chunks ──────────────┐
         kinds,    ├─ dense: bge(query) · chunk vectors ─────┼─▶ collapse to file ─▶ RRF (k=60) ─▶ rerank ─────▶ results
         dates,    └─ CLIP: clip_text(query) · images ─▶ gate┘   (best chunk/file)                  (text hits,    + highlight
         "phrases"                                                                                   in place)      boxes
                   all three apply the parsed filters first
```

**Boundary rule:** `vault/` (core) has no UI imports. `app.py` and `vault/ui/` call only `vault.search.engine.search()` and `vault.ingest.pipeline.index_paths()`.

---

## 4. Tech stack

| Concern | Choice | Notes |
|---|---|---|
| Text embeddings | `fastembed` → `BAAI/bge-small-en-v1.5` (384-d, ~67 MB) | ONNX, CPU |
| Image embeddings and query→image | `fastembed` → `Qdrant/clip-ViT-B-32-vision` + `Qdrant/clip-ViT-B-32-text` (512-d, shared space) | Text side is capped at 77 tokens, which is fine for queries |
| Reranker | `fastembed` `TextCrossEncoder` → `Xenova/ms-marco-MiniLM-L-6-v2` | Togglable |
| OCR | `rapidocr` + `onnxruntime` | Since v2 the inference engine is installed separately. Returns a box, text and score per line. |
| PDF | `pymupdf` | Text per page, image positions (`get_image_info`), clipped renders, `search_for` for highlight rectangles |
| Word / PowerPoint | Document, slide and notes XML read straight from the zip (stdlib `zipfile` + `ElementTree`) | No Office install needed. python-docx and python-pptx load every part, media included, into memory, so they're used only to build test fixtures and as the equivalence reference |
| Images / EXIF | `Pillow` (+ optional `pillow-heif` for HEIC) | EXIF IFD `0x8769`, GPS IFD `0x8825` |
| Storage | SQLite (stdlib) with FTS5; vectors stored as float32 BLOBs and loaded into NumPy | No vector DB (§11) |
| UI | `gradio` (Blocks + custom CSS) | Runs unchanged locally, on HF Spaces or in Docker |
| CLI | `argparse` (+ `rich`) | `python -m vault ...` |
| Tests | `pytest` | Fixtures are generated at test time; no personal files |

No PyTorch anywhere. Model weights download once from the HF Hub into a gitignored cache dir.

---

## 5. Data model (SQLite, `data/vault.db`)

The store is **content-addressed**: one `files` row per unique content (sha256), and any number of paths pointing at it. A rename, a move or a duplicate is just a `paths` change, so it never triggers re-extraction or re-embedding.

```sql
CREATE TABLE files (
  id INTEGER PRIMARY KEY,
  sha256 TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('pdf','doc','slides','screenshot','photo')),
  kind_reason TEXT,                    -- e.g. 'ext:.pptx', 'filename:Screenshot*', 'exif:Make', 'png-no-exif'
  effective_date TEXT,                 -- ISO-8601
  date_source TEXT,                    -- 'exif' | 'filename' | 'doc_meta' | 'mtime'
  width INTEGER, height INTEGER,
  pages INTEGER,                       -- PDF pages or pptx slides
  gps_lat REAL, gps_lon REAL,
  thumb_path TEXT,
  status TEXT NOT NULL DEFAULT 'ok',   -- 'ok' | 'error' | 'skipped'
  error TEXT,
  indexed_at TEXT
);
CREATE TABLE paths (
  path TEXT PRIMARY KEY,               -- absolute, via pathlib (Windows-safe)
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  root TEXT NOT NULL,                  -- the indexed folder it was found under (scopes the sweep)
  size_bytes INTEGER NOT NULL, mtime REAL NOT NULL,
  seen_run INTEGER NOT NULL            -- last index run that saw it
);
CREATE TABLE chunks (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  page INTEGER,                        -- PDF page or pptx slide (1-based); NULL otherwise
  ord INTEGER NOT NULL,
  source TEXT NOT NULL,                -- 'pdf_text' | 'ocr' | 'doc_text' | 'slide_text' | 'path'
  text TEXT NOT NULL
);
CREATE TABLE ocr_lines (
  id INTEGER PRIMARY KEY,
  chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  line_no INTEGER NOT NULL, block_no INTEGER NOT NULL,
  text TEXT NOT NULL, conf REAL,
  x0 REAL, y0 REAL, x1 REAL, y1 REAL   -- images: original-image pixels; PDFs: page points
);
CREATE VIRTUAL TABLE chunks_fts USING fts5(text, tokenize='porter unicode61');  -- rowid = chunks.id
CREATE TABLE text_vectors  (chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE, vec BLOB NOT NULL); -- float32[384], L2-normalised
CREATE TABLE image_vectors (file_id  INTEGER PRIMARY KEY REFERENCES files(id)  ON DELETE CASCADE, vec BLOB NOT NULL); -- float32[512], L2-normalised
CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, report_json TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);  -- schema_version, model names, last_indexed
```

- **One transaction per file**, covering its files row, chunks, FTS rows, OCR lines and vectors. An interrupted run therefore loses at most the file in progress, and the next run resumes through the fast path. FTS5 has no foreign-key cascade, so FTS rows are deleted explicitly.
- `meta` stores the embedding model names. If they don't match the configured models at startup, refuse to search and tell the user to run `vault reindex`.
- Every file has one `source='path'` chunk listing the filenames and parent folder names of **all** its paths, e.g. `Goa trip 2025 / IMG_2041.jpg`. Folder names carry real signal, and this gives image-only files something for BM25 to match. Rebuild and re-embed this one chunk whenever the file's paths change.
- When a file loses its last path, delete it with all its rows.

---

## 6. Ingestion

### 6.1 Discover and incremental indexing
1. Walk the configured roots. Allowed extensions: `.pdf .docx .pptx .txt .md .png .jpg .jpeg .webp .heic` (HEIC only if `pillow-heif` is installed). Skip hidden folders, system folders, and Office lock files (`~$*`).
2. **Fast path:** if a known path has the same size and mtime, mark it seen without reading it.
3. Otherwise, hash the file with sha256:
   - **hash already known:** point the path at the existing file. This covers renames, moves and duplicates with no re-extraction.
   - **hash new:** queue the file for extraction. If the path previously pointed at other content, re-point it; the old content is deleted if no paths remain.
4. **Process the queue newest-first** (by mtime, descending), so recent files become searchable within minutes on a large first index.
5. **Sweep:** delete path rows under the walked roots that this run didn't see. Then delete files left with no paths, with all their rows.
6. Errors (corrupt file, encrypted PDF, decode failure, zip guard) set `status='error'` or `'skipped'` with a message, and the run continues. Ctrl+C finishes the current file, then stops and prints the report.
7. **Report** (also saved in `runs`): added, linked (renames and duplicates), updated, deleted, errors, time per stage, and files/sec per kind. These numbers go into the README; don't guess them.

### 6.2 Classify
A rule-based function in `classify.py` returns `(kind, kind_reason)`. Each rule gets its own unit test.
- `.pdf` → `pdf`; `.docx .txt .md` → `doc`; `.pptx` → `slides`.
- Images, rules in order:
  1. A screenshot-style filename → `screenshot`. Patterns:
     - Windows Snipping Tool: `Screenshot 2026-10-16 170612.png`
     - Win+PrtScn: `Screenshot (12).png`
     - Android: `Screenshot_20251012-101530_*.jpg`
     - macOS: `Screen Shot 2025-03-12 at …`, `Screenshot 2025-03-12 at …`
     - generic `scr_`, `screencapture`
  2. EXIF has `Make`/`Model` → `photo`.
  3. A PNG with no camera EXIF → `screenshot`.
  4. No EXIF and dimensions matching a common screen resolution → `screenshot`.
  5. Otherwise → `photo`.

### 6.3 Extract

**PDF**
- Native text per page via PyMuPDF.
- **Scanned pages:** if a page has fewer than 20 characters of text, render the whole page at 150 DPI and OCR it.
- **Image regions:** for each embedded image whose bounding box covers ≥ 15% of the page area, render just that region (`get_pixmap(clip=bbox, dpi=150)`) and OCR it. This catches slides exported to PDF where the title is text but the body is a picture. Skip a region that already has ≥ 20 characters of native text inside it (a scan with a text layer is already searchable).
- OCR at most 30 pages per PDF (configurable), and log when the cap is hit.
- Store OCR line boxes in page points. Encrypted PDFs → `skipped`.

**Office and text**
- `.docx` (`word/document.xml` parsed straight from the zip; media is never read): body paragraphs in order, then table cells row by row, with the same text as python-docx (tabs, line breaks, hyperlinks, merged cells). Word doesn't store page numbers, so `page` is NULL.
- `.pptx` (slide, notes and core-properties XML parsed straight from the zip; media is never read): per slide, the title, every text frame (including inside groups), tables and speaker notes; `page` = slide number. Pictures inside slides are not OCR'd (known limitation, §17).
- `.txt` / `.md`: read as UTF-8 with `errors="replace"`.
- **Zip guard:** `.docx` and `.pptx` are zip archives. It targets zip bombs, not big decks. Before parsing, refuse a file if any member over 1 MB unpacked has a compression ratio above 100 (media compresses about 1:1, so a bomb can't hide among it), or if its XML parts (`.xml`, `.rels`; media not counted) exceed 50 MB unpacked. An absolute cap on total unpacked size (`zip_max_total`) is off in local mode and set only for demo uploads.

**Images (OCR)**
- OCR **every** image, photos included, because whiteboards and signs matter.
- **Tile, don't shrink:**
  - If the width is over 2048 px, scale it down to 2048 px wide.
  - If the height is then still over 2048 px, cut the image into 1,600 px strips with 200 px overlap (at most 15 strips; a taller image is scaled down until it fits in 15).
  - OCR each strip, map the boxes back to original-image pixels, and drop duplicate lines from the overlaps (same normalised text, vertical centres within half a line height). A line touching a strip's inner cut edge is dropped too; the overlap guarantees the neighbouring strip has it whole.

  This is why a 1080×6000 scrolling screenshot stays readable.
- Apply the EXIF orientation first; boxes are in the upright image's pixels.
- Drop OCR lines with confidence below 0.5.
- **Digit fixes (OCR text only, never native PDF/Office text):** `O`/`o` + `x` + ≥ 4 hex digits becomes `0x…`, and an `O` between digits becomes `0`. `ocr_lines` stores the same corrected text as the chunk.
- **Reading order:**
  1. Split the lines into regions with a recursive XY-cut. Cut at a vertical gutter (empty across the whole region, with at least two rows on each side) if there is one; otherwise at horizontal whitespace bands wider than 1.5× the median line height. Adjacent bands that share a column gutter are rejoined first, so paragraph breaks that line up across columns don't interleave them.
  2. Inside each region, sort lines by vertical centre. Lines that overlap vertically by ≥ 50% form one row, sorted left to right.
  3. Each region starts a new block, and so does a gap to the previous row of more than 1.5× the median line height.
- Store every line in `ocr_lines` with its box, `block_no` and confidence.

**Effective date** (first source that works, stored in `date_source`)
1. EXIF `DateTimeOriginal`;
2. a date in the filename (the screenshot patterns above, `IMG_20250312_…`, `PXL_2025…`);
3. document metadata (`doc_meta`): PDF `creationDate`, or Office core property `created`;
4. file mtime.

Files that have lost their metadata (for example, images forwarded on WhatsApp, or `Screenshot (12).png`) fall back to mtime.

**GPS:** read from EXIF if present. It is stored only; it's used in a stretch goal.

**Thumbnails:** 320 px on the long side, WEBP, at `data/thumbs/<sha256>.webp`.
- Images: the image itself.
- PDFs: page 1.
- `.docx`, `.pptx`, `.txt`, `.md`: a generated card (file-type badge plus the title or first line), since rendering Office files would need LibreOffice.

### 6.4 Chunk
- **PDF pages and pptx slides:** one chunk per page or slide. If it's over 1,200 chars, split it into ~800-char windows with 150-char overlap, keeping the page number.
- **docx / txt / md:** ~800-char chunks cut on paragraph boundaries, with 150-char overlap.
- **OCR text:** chunks are built from whole layout blocks, up to ~800 chars. A block is never split unless it alone exceeds 1,200 chars. A typical screenshot ends up as one chunk.
- Normalise whitespace and keep the original casing; FTS5 handles case-folding.

### 6.5 Embed
- Text chunks go to bge-small in batches of 32; images go to CLIP-vision in batches of 16.
- L2-normalise everything before storing.
- Time each stage (§6.1, step 7).

---

## 7. Search

### 7.1 Query parsing (`search/parse.py`, rule-based, deterministic)
Input: the raw query and `now` (injectable, for tests). Output: `ParsedQuery(text, kinds, date_from, date_to, phrases)`.

- **Kind words** become hard filters and are removed from the text that gets embedded:

  | Words | Kinds |
  |---|---|
  | screenshot(s) | `screenshot` |
  | photo(s), picture(s), pic(s) | `photo` |
  | image(s) | `photo` + `screenshot` |
  | pdf(s) | `pdf` |
  | document(s), doc(s), notes, paper(s) | `pdf` + `doc` |
  | word file, docx | `doc` |
  | slide(s), ppt, pptx, presentation(s), deck | `slides` |

- **Dates:**
  - "today", "yesterday";
  - "this/last week|month|year";
  - "in March" (the most recent past March);
  - "in 2025", "March 2025";
  - "before/after 2025".

  Date phrases are removed from the text.
- **Quoted phrases** become required FTS5 phrases.
- Glue words left at the edges after removal ("of", "from", "my") are stripped.
- If no text is left, it's a filter-only search: no retrievers run, and results are sorted by date, newest first.
- The UI shows parsed filters as removable chips.

Example: `screenshot of the access denied error from last week` → kinds={screenshot}, dates = last calendar week, text=`access denied error`.

### 7.2 Retrievers (all three apply the kind and date filters before ranking)

| Retriever | Scores | Top-k |
|---|---|---|
| BM25 | `bm25(chunks_fts)` over chunks; query = text tokens (OR) plus required phrases (AND) | 100 chunks |
| Dense text | cosine(bge(text), text_vectors) | 100 chunks |
| CLIP | cosine(clip_text(text), image_vectors), then the gate (§7.3) | ≤ 50 files |

`vectors.py` provides `VectorIndex`. It loads a vector table into a NumPy matrix with a row→id map, runs exact top-k by matmul with `argpartition`, and supports a filter mask. Rebuild it after indexing.

### 7.3 CLIP gate (`search/gate.py`)
**The problem:** CLIP returns a full ranking for every query, even `error 0x80070005`. In RRF, CLIP's rank 1 counts exactly as much as BM25's rank 1, so a random photo can tie with the correct screenshot.

**The gate:**
- Compute z = (score − mean) / std over all filter-passing candidate images, and keep only hits with **z ≥ 2.0** (configurable), at most 50.
- If there are fewer than 20 candidates, keep the top 5 instead.
- If the query contains a quoted phrase or a code-like token, multiply CLIP's RRF weight by 0.5 (configurable). A code-like token is either a hex value like `0x80070005`, a run of 6 or more digits, or a token of 4 or more characters mixing letters and digits.
- The gate logic is unit-tested on synthetic score arrays. The eval compares gated vs ungated (§9).

### 7.4 Fusion
1. **Collapse to file:** for each retriever, keep each file's best-ranked chunk. That rank is the file's rank in that list, and the chunk is remembered as the snippet candidate.
2. **Reciprocal Rank Fusion:** `score(f) = Σ_r w_r / (k + rank_r(f))`, with `k = 60` and `w_r = 1.0` by default, adjusted by the gate. A file missing from a list contributes 0 for that list.
3. **Snippet:** the best chunk from BM25 if present, else from dense. Query terms are highlighted with `<mark>` in a ~200-char window. Image-only hits get no snippet and a "visual match" badge.

### 7.5 Rerank (togglable, on by default)
- Take the top 30 after RRF. Only results with text (OCR, PDF, doc or slide chunks) can be reranked, because the cross-encoder needs text.
- Score `(query text, best chunk text)` with the cross-encoder.
- **Reorder the text-bearing results among the slots they already hold, and leave image-only results at their fused positions.** That way a model that can't see pixels never pushes out a visual match.

### 7.6 Highlights (`search/highlight.py`)
- **Images:** draw boxes on the lines in the matched chunk that contain a query term. If none do (a meaning-based match), box the whole best block. The boxes are translucent rectangles drawn with Pillow on the preview.
- **PDF pages:** use `page.search_for(term)` on native text at render time, plus the stored OCR line boxes for OCR'd regions.
- **docx / pptx / txt / md:** text highlighting with `<mark>` only.
- **CLIP-only matches:** no box; they carry the "visual match" badge.

### 7.7 Result payload
`{file_id, kind, filename, folder, paths, copies, effective_date, page, thumb_url, snippet_html, highlight_boxes, badges, ranks: {bm25, dense, clip}, clip_gated, rrf_score, rerank_score}`

- `paths` lists every copy of the file. The primary path is the most recently modified one.
- `ranks` powers the "Why this result?" panel.

### 7.8 Performance budget
- With ≤ 5k files on 2 vCPU, rerank on: query p50 ≤ 300 ms, p95 ≤ 1 s.
- Models load once, as lazy singletons in `models.py`, and are warmed up at app start with a dummy query.

---

## 8. UI (Gradio Blocks)

**Search tab**
- Search box (Enter to submit), kind filter chips, removable parsed-filter chips, a rerank toggle, and a "show scores" debug toggle.
- Responsive card grid. Each card shows a thumbnail, filename, date, kind badge, page or slide number, snippet, and "+N copies" when there are duplicates.
- Clicking a card opens a detail panel with:
  - a larger preview with highlight boxes (for a PDF, the matched page rendered);
  - the full matched chunk with highlights;
  - "Why this result?": per-retriever ranks, the RRF arithmetic, and whether CLIP was gated;
  - all paths of the file;
  - "Open file" (local mode only).
- Empty state: example queries as clickable chips.

**Index tab (local only)**
- Add or remove folders, and an "Index now" button with a progress bar.
- Last run report, counts by kind, and an error list.

**Upload tab (demo only)**
- Upload individual files, or a whole folder via `gr.File(file_count="directory")`.
- **Limits:** ≤ 100 files, ≤ 25 MB each, ≤ 200 MB total per session, with an extension allow-list.
  - Enforce the limits in our own code, not only through Gradio's settings.
  - Set the final caps after measuring indexing speed in M7. Target: a 100-file mixed upload indexes in ≤ 5 min on the deploy host (§15).
- A progress bar with per-file status; failed files show why.
- **Session index:** on the first upload, copy the demo index (SQLite backup API) into `/tmp/vault_sessions/<uuid4>/` and index the uploads into that copy. The demo index itself never changes.
- **Scope toggle:** *My uploads* (the default once something is uploaded), *Sample corpus*, or *Both*.
- Wipe a session after 30 min idle, and wipe all sessions on app start.
- Visible notice: "Uploads are processed on this server, kept only for your session, and deleted after 30 minutes. For private use, run it on your own computer (see README)."

**How it works tab**
- Architecture diagram, the eval ablation table (rendered from `eval/results.md`), a privacy note, and a link to "Run on your own files".

Thumbnails and previews are served only by file id or session id. Gradio's `allowed_paths` covers just the thumbs dir and the session root.

---

## 9. Evaluation (`python -m vault eval`)

- **Queries** in `eval/queries.jsonl`, one object per line:
  ```json
  {"id":"q07","query":"screenshot of the access denied error","category":"exact","relevant":["corpus/screens/err_0x80070005.png"]}
  ```
  Categories:
  - `exact`: codes, IDs, rare strings;
  - `semantic`: a paraphrase of the content;
  - `visual`: the target has no text;
  - `filtered`: kind or date words;
  - `mixed`.
- **At least 40 queries, at least 6 per category.** The set must also include:
  - ≥ 6 queries whose targets are `.docx` / `.pptx` files;
  - ≥ 3 targeting text in tall screenshots;
  - ≥ 3 targeting text inside images on PDF pages.

  Write the queries *before* looking at results.
- Relative-date queries run with a **fixed `now`** set in the eval config, so results don't drift with the calendar.
- **Metrics:** Recall@1, Recall@5, Recall@10 and MRR@10, overall and per category. Also query latency p50/p95.
- **Ablations** (one row each):
  1. BM25 only
  2. dense only
  3. CLIP only
  4. BM25 + dense
  5. all three, CLIP ungated
  6. all three, CLIP gated
  7. all three, gated + rerank
- **Output:** `eval/results.md` (a table plus a timestamp, corpus size and model names) and `eval/results.json`.
- **Expected story:**
  - BM25 wins `exact`, dense wins `semantic`, CLIP wins `visual`.
  - The gate lifts `exact` without hurting `visual`.
  - The full system is best or near-best everywhere.

  Report what actually happens, failures included.

---

## 10. Demo corpus (`corpus/`, committed, non-personal only)

Aim for about 200 files:
- **~30 PDFs:** arXiv papers (check the licence; many are CC-BY) and public documents.
  - Include ≥ 2 scanned PDFs and ≥ 3 slide-style PDFs with image-heavy pages.
  - **No course notes**; the professor's copyright applies.
- **~15 documents and decks you write yourself:** a few `.pptx` decks (e.g. on gradient descent or SVD), `.docx` notes and `.md`/`.txt` notes.
- **~70 screenshots** of public or non-sensitive content: error dialogs, code, terminal tracebacks, web pages, settings, charts. Include ≥ 5 tall phone scrolling screenshots.
- **~70 photos:** your own non-personal photos (no faces) plus CC0 images from Unsplash, Pexels or Wikimedia.

Requirements:
- Remove EXIF GPS from your own photos (`python -m vault scrub-exif corpus/photos`).
- `corpus/MANIFEST.csv`: `path, kind, source_url_or_own, licence`.
- The corpus contains a target for every §12 query.

**Start collecting today:** it's manual work that doesn't depend on any code. A first slice of ~50 files is enough for the M4 deploy.

---

## 11. Design decisions (be ready to defend these)

1. **Hybrid retrieval.** BM25 catches exact tokens such as error codes and IDs, which embeddings blur. Dense embeddings catch paraphrase ("bill" ↔ "invoice"). CLIP catches content with no text at all.
2. **RRF instead of score blending.** BM25, bge cosine and CLIP cosine live on different, uncalibrated scales; CLIP cosines cluster around 0.2–0.35. RRF uses only ranks, so it needs no calibration.
3. **Gate CLIP before fusing.** CLIP always returns a ranking, even when no image answers the query. Without a gate, its rank 1 ties with BM25's correct rank 1. Keeping only statistical outliers lets visual matches through when they exist and keeps CLIP out when they don't. The ablation measures this.
4. **Exact brute-force search, no vector DB.** 5k × 384 float32 is about 7.5 MB, and a matmul over it takes milliseconds with zero recall loss compared to approximate nearest-neighbour (ANN) search. It sits behind `VectorIndex`, so switching to FAISS or LanceDB later is a contained change.
5. **ONNX via fastembed and RapidOCR, not PyTorch.** The install is smaller and CPU inference faster, with the same models.
6. **Chunk by structure.** PDFs are chunked by page and decks by slide, because the page or slide number is what a user wants to jump to. OCR text is chunked by layout block: OCR returns lines in detection order, which interleaves sidebars with the main content, so sorting into reading order and chunking by block keeps each chunk about one thing.
7. **Content-addressed storage.** There is one row per unique content and many paths. Renames, moves and duplicates are just path updates and are never re-embedded, and results show one card per content with "+N copies".
8. **Tile tall images instead of shrinking them.** Shrinking a 1080×6000 scroll to 2048 px tall makes its text about 3× smaller and unreadable to OCR. Strips at native resolution keep it readable.
9. **Rerank only text-bearing results, in place.** A cross-encoder can't see pixels, so it shouldn't demote visual matches.
10. **Rule-based query parser, not an LLM.** It's deterministic, unit-testable, instant and works offline. An LLM parser is future work.
11. **Privacy by construction.** Local mode binds to `127.0.0.1`. After the first model download it runs with `HF_HUB_OFFLINE=1` and makes no network calls at all. Demo mode never reads a user-supplied path, and it states plainly that uploads go to its server.

---

## 12. Example queries that must work on the demo corpus (smoke test)

| Query | Expected top-5 hit |
|---|---|
| `error 0x80070005` | screenshot of that Windows error |
| `python traceback KeyError` | terminal screenshot with that traceback |
| `paper about denoising diffusion models` | the diffusion PDF (page 1) |
| `where is singular value decomposition defined` | the right PDF **and page** |
| `slide about gradient descent` | that `.pptx`, correct slide number |
| `sunset at the beach` | beach-sunset photos |
| `whiteboard with equations` | whiteboard photo (CLIP or OCR) |
| `<a phrase near the bottom of a tall scrolling screenshot>` | that screenshot, with the line boxed |
| `<text that only appears inside an image on a slide-style PDF page>` | that PDF and page |
| `screenshots from last week` (fixed `now`) | only screenshots in that date range, newest first |
| `"<an exact sentence copied from one corpus PDF>"` | that PDF and page (exact-phrase path) |

`tests/test_smoke.py` runs these against the demo index. It's skipped if the index is missing.

---

## 13. Repo layout

```
personal-search-vault/
  CLAUDE.md  SPEC.md  README.md  requirements.txt  .gitignore
  setup.bat  setup.sh            # check Python 3.12, create venv, install, launch
  app.py                         # Gradio entry; reads VAULT_MODE
  probe/                         # M0 deploy probe (delete after M4)
  deploy/                        # azure.md runbook, systemd unit, Caddyfile (§15)
  bench/                         # bench_ocr.py compare_ocr.py make_bench_corpus.py README.md: reproduce the §19 numbers
                                 #   (output in bench/generated/, gitignored)
  vault/
    __main__.py                  # CLI: index | reindex | search | stats | eval | scrub-exif | build-demo
    config.py                    # dataclass config + env overrides; data dir, roots, weights, gate, limits
    db.py                        # schema, migrations, transactions, FTS sync
    models.py                    # lazy singletons: text_embedder, clip_text, clip_vision, ocr, reranker
    vectors.py                   # VectorIndex
    platform.py                  # OS-specific bits: open file, default folders
    ingest/  discover.py classify.py extract_pdf.py extract_office.py extract_image.py
             ocr.py (tiling, reading order, blocks) chunk.py thumbs.py pipeline.py
    search/  parse.py retrievers.py gate.py fuse.py rerank.py snippet.py highlight.py engine.py
    session.py                   # demo-mode per-session index copy + TTL cleanup
    eval.py
    ui/      layout.py cards.py styles.css
  corpus/   (+ MANIFEST.csv)   demo_index/ (vault.db + thumbs, built by `vault build-demo`)
  eval/     queries.jsonl results.md results.json
  tests/    conftest.py (generates fixtures) test_*.py
  data/     (gitignored: local vault.db, thumbs, model cache)
```

---

## 14. Milestones and acceptance criteria

The plan has one Claude Code session per milestone, with a commit at the end of each. This is one of four viva projects, so the plan front-loads a **submission-safe core** (M0–M4: working search, deployed). M5–M8 are polish to be done as time allows, trimmed using the cut list below if needed.

### Core (must ship)

| # | Date | Milestone | Done when |
|---|---|---|---|
| M0 | Fri 16 Oct | **Scaffold + deploy probe** | Repo, git, venv on Py 3.12, `requirements.txt`, `pytest` green on an empty suite. `probe/app.py` is a hello-world Gradio app that loads bge-small via fastembed and embeds one query; the probe runs on the chosen host (§15). The deploy path is recorded in §19. |
| M1 | Sat 17 Oct | **Text pipeline** | Content-addressed discover (fast path, hashing, linking renames and duplicates, newest-first, sweep, resumable). PDF native text; docx/pptx/txt/md with the zip guard; chunking; FTS5 + bge; RRF over 2 lists; CLI `index`, `search`, `stats`. **Tests:** a PDF fixture returns the right **page**, and a pptx fixture the right **slide**. A docx fixture is found by paraphrase. Copying a file adds a path but no chunks. A rename re-embeds no content chunks (only the file's one `path` chunk, §5). Deleting the last path removes all rows. |
| M2 | Sun 18 Oct | **OCR** | RapidOCR on all images: tiling, reading order, blocks, block-aware chunks, `ocr_lines`. PDF scanned-page and image-region OCR. **Tests:** a Pillow-drawn `ERROR 0x80070005` is the top hit for `0x80070005`. Text in the bottom strip of a 1080×6000 fixture is found, and its box is in the right place. A two-column fixture gives two blocks rather than interleaved lines. Text drawn inside an embedded image on a PDF page is found. **Measured** files/sec per kind recorded in §19. |
| M3 | Mon 19 Oct | **Photos, CLIP, metadata** | CLIP vision/text as the 3rd list, with the gate (unit-tested on synthetic scores). Classifier with a test per rule, including Snipping Tool names. Date extraction with a test per source. Thumbnails for all five kinds. |
| M4 | Tue 20 Oct | **UI + first deploy (submission-safe checkpoint)** | Search tab: cards, thumbnails, snippets, kind chips. Index tab. Cross-platform "Open file". `VAULT_MODE=demo` with a read-only demo index built from a ~50-file first slice of the corpus. Basic multi-file upload into a session copy of the index. Deployed, and the URL works from a phone. **After M4 plus transcripts, the submission is complete.** |

### Polish (target dates are flexible; the other viva projects may come first)

| # | Date | Milestone | Done when |
|---|---|---|---|
| M5 | Wed 21 Oct | **Parser + rerank** | Parser tests for every §7.1 pattern, with a fixed `now`. Filter chips in the UI. Rerank with the in-place rule from §7.5, tested. |
| M6 | Thu 22 Oct | **Full corpus + eval** | Corpus at ~200 files plus MANIFEST. ≥ 40 queries meeting the §9 coverage. `vault eval` writes the 7-row ablation table and latency. Smoke tests (§12) pass. Demo index rebuilt and redeployed. |
| M7 | Fri 23 Oct | **Detail panel + uploads v2** | Highlight boxes, "Why this result?", "+N copies", all paths. Folder upload, scope toggle, progress, limits, TTL. A 100-file upload is timed on the deploy host and the final caps are set. |
| M8 | Sat 24 Oct | **Pack** | README: the problem, a GIF, architecture, the §11 decisions, the eval table, measured ingest numbers, §17 limitations, and "Run on your own files". `setup.bat` / `setup.sh`, with the install tested on a **different computer**. Offline demo rehearsed (§16). |
| — | Sun 25 – Mon 26 Oct | Buffer | Fix whatever broke. No stretch goals unless M0–M8 are done. |
| — | **Tue 27 Oct, before 2 PM** | Submit | URL + transcript HTML on MS Teams. |

**Cut order if behind** (drop from the top):
1. Folder upload and the scope toggle. Keep multi-file upload.
2. Highlight boxes. Keep text snippets; `ocr_lines` is still stored, since that's cheap.
3. Rerank.
4. Relative-date parsing. Keep the kind words.
5. Eval rows 4–7. Keep rows 1–3 plus the full system.

**Never cut:** a working deploy, the transcripts, and a README with honest limitations.

**Stretch goals** (only after M8):
- a `watchdog` live re-index with a ~2 s debounce, because the OS fires several events per save;
- offline reverse geocoding for "photos from Goa" (`reverse_geocoder`);
- near-duplicate clusters (one matmul over the CLIP vectors);
- regex auto-tags (amounts, emails, URLs) as filter chips;
- OCR for pictures inside `.pptx` / `.docx`;
- `.xlsx` support;
- multilingual models;
- an optional LLM query parser behind a flag.

---

## 15. Deployment (facts checked 14 Oct 2026)

**Current Hugging Face rules:**
- Creating Gradio or Docker Spaces on CPU Basic now needs a paid plan (PRO, $9/month).
- Free personal accounts with a verified email that are older than 30 days can host **up to 2 Gradio Spaces on ZeroGPU**.
- Static Spaces stay free.
- ZeroGPU supports the Gradio SDK only, on Python 3.10.13 or 3.12.12.
- Spaces on free hardware sleep when idle, and their disk is ephemeral.

**Hosting budget:** those 2 free ZeroGPU slots are shared across all four viva projects. Decide in M0 which projects get them.

| Option | Cost | Pros | Risks |
|---|---|---|---|
| **A. Gradio Space on ZeroGPU** | Free | Public URL, same code | Uses 1 of 2 free slots. It's GPU-oriented hardware, so verify that CPU ONNX code runs. If startup complains about a missing `@spaces.GPU`, decorate the query-encode function; the decorator is a no-op elsewhere. |
| **B. HF PRO for one month → Gradio Space on CPU Basic (2 vCPU / 16 GB)** | $9 | Most predictable; also gives 10 ZeroGPU slots for the other projects | Needs a card |
| **C. Laptop + Cloudflare quick tunnel** | Free | Zero setup | URL changes every run and only works while the laptop is on. Use it as a backup only. |
| **D. Azure for Students VM + systemd + Caddy** | Paid from the Azure for Students credit | Plain Linux box: no GPU runtime or PyTorch assumptions, persistent disk, never sleeps, uses no HF slot. 2 vCPU matches the §7.8 budget. Caddy gets and renews a Let's Encrypt certificate on its own. | We run the server ourselves (updates, restarts, disk). Only 4 GiB RAM, so a 4 GB swap file. Always on (auto-shutdown off), so it draws on the credit continuously; check the balance. |

**Decision (M0, 16 Oct): D.** The VM is ready, and it avoids ZeroGPU's PyTorch/GPU-oriented runtime entirely. A stays untried, so both free ZeroGPU slots remain for the other viva projects. Keep C ready as a fallback.

**The D host:**
- Ubuntu 24.04, `Standard_B2ls_v2` (2 vCPU, 4 GiB RAM), India South Central, user `azureuser`. At M4 it is resized in place to `Standard_D4as_v4` (4 vCPU, 16 GB, non-burstable) by Stop → Size → Start, keeping its disk, IP, NSG and host key (open item h, `deploy/azure.md` §9). The VM is needed only until 30 Oct 2026.
- DNS `swarup-vault.indiasouthcentral.cloudapp.azure.com`. Inbound 22, 80 and 443 only.
- The app binds `127.0.0.1:7860` and runs as a systemd service. Caddy is the only public listener: it serves HTTPS on 443 and redirects 80.
- Code arrives by `git push` to a bare repo on the VM (`~/vault.git`); the checkout at `~/personal-search-vault` pulls from it. No third-party git host.
- Model cache lives in `data/models/` on the VM (`FASTEMBED_CACHE_PATH`), so reboots don't re-download.
- The runbook is `deploy/azure.md`.

**Mechanics:**
- `vault build-demo` builds `demo_index/` locally, and it gets committed. The server never indexes the corpus at startup.
- Models download on first start.
- The VM doesn't sleep, but check the URL before anyone looks at it.

---

## 16. Demo plan (for the professor's review, interviews or a live demo)

1. **Say up front what the URL is:** "This link is a demo copy on a cloud server with public sample files, so you can try it. The real product runs on your own computer, where your files and the models never leave the machine."
2. **Prove it works offline.**
   - The day before, with Wi-Fi on, run local mode once, so the models are cached and the index is built.
   - In the demo, start it offline:
     ```
     $env:HF_HUB_OFFLINE="1"
     python app.py
     ```
   - Open `http://127.0.0.1:7860` and **turn Wi-Fi off where they can see it**.
   - Run an exact query (an error code), a meaning query (a PDF topic) and a visual one ("sunset at the beach").
   - Open "Why this result?" on one of them, and point at `data/vault.db`: the whole index is one file on your disk.
3. **Their own files:** upload a folder on the demo with scope set to *My uploads*, or install it with `setup.bat` / `setup.sh`.
4. **State the limitations (§17) before anyone finds them.**

---

## 17. What it searches well, partly, and not at all

**Searches well**
- Digital PDFs: papers, notes, tickets, bills. Both exact words and meaning, with the right page.
- Word, PowerPoint, text and Markdown files, with the right slide for decks.
- Screenshots of on-screen text, including tall scrolls.
- Exact strings: IDs, PNRs, error codes, names.
- Photos of common scenes and objects.

**Partly works**
- Scanned PDFs: depends on scan quality, and OCR is capped at 30 pages.
- Photographed text: angled or blurry signs, documents, whiteboards.
- Math, tables and charts: the surrounding words are found, but equations come out garbled.
- Date filters for files that lost their metadata, which fall back to mtime.

**Doesn't work**
- Handwriting.
- Non-English text: the models are English-only, and the default OCR models read English and Chinese.
- Specific people, since there's no face recognition.
- Place names, except famous landmarks. GPS lookup is a stretch goal.
- Counting and negation ("exactly 3 people", "without the sidebar").
- Password-protected PDFs, pictures inside Word/PowerPoint files, and `.doc`, `.ppt`, `.xlsx`, video, audio, email and archives.

---

## 18. Security and privacy checklist

- [ ] Local mode binds `127.0.0.1`; only demo mode is exposed.
- [ ] On the VM, Gradio binds `127.0.0.1:7860`; only Caddy (80/443) and SSH (22, key-only) are reachable.
- [ ] Demo mode resolves every served file from a DB id or a session id. No path parameters.
- [ ] Session ids are `uuid4`; session dirs live under one root and are wiped on TTL and on start.
- [ ] Uploads: extension allow-list; size, count and total limits enforced in code; zip guard for `.docx` / `.pptx`; every decode wrapped in try/except.
- [ ] The upload notice says plainly that uploads are processed on the server.
- [ ] Nothing personal in git: no personal files, no GPS EXIF, no model cache, no `data/` (`.gitignore` covers them).
- [ ] No telemetry. The only network calls are model downloads, and none at all with `HF_HUB_OFFLINE=1`.

---

## 19. Decisions log (append as you go)

| Date | Decision | Why |
|---|---|---|
| 14 Oct | Fully local ONNX models; Gradio UI; hybrid BM25 + bge + CLIP fused with RRF | §11 |
| 16 Oct | v2 scope: Office files, OCR tiling and reading order, PDF image-region OCR, CLIP gate, content-addressed store, highlights, uploads v2 | §20 |
| 16 Oct | Deploy path **D**: Azure for Students VM (Ubuntu 24.04, B2ls_v2), systemd, Caddy for HTTPS; the app binds 127.0.0.1 | §15. No PyTorch/GPU runtime risk, persistent disk, no sleep, keeps both ZeroGPU slots free for other projects |
| 16 Oct | M0 probe verified on the VM: https://swarup-vault.indiasouthcentral.cloudapp.azure.com/ returns 384-d, norm 1.0 vectors identical to the laptop's; warm encode 7–10 ms; probe RSS 372 MB, Caddy 56 MB; port 7860 unreachable from outside; HTTP → HTTPS 308 | Measured 16 Oct with `gradio_client` from the laptop |
| 16 Oct | Code reaches the VM via a bare repo on the VM (`git push vm main`), not GitHub | No third-party host or deploy key; same SSH key as admin access |
| 16 Oct | Local Python 3.12 is uv-managed (`uv python install 3.12`; venv made with `--seed` so pip works) | No 3.12 or `py` launcher on the laptop; uv was already installed |
| 16 Oct | Full core stack pinned in `requirements.txt` at M0 | Surfaces Windows/Linux wheel problems before M1–M3 |
| 17 Oct | A rename or copy re-embeds only the file's `path` chunk (one vector); content chunks keep their ids and vectors. M1's test is worded to match | Resolves §5 (rebuild the path chunk on path change) vs §14 ("a rename re-embeds nothing"); folder names stay in dense search |
| 17 Oct | M1 discovers only `.pdf .docx .pptx .txt .md`; image extensions join discovery when OCR lands (M2) | Images indexed without OCR would be skipped by the fast path later and force a `reindex` |
| 17 Oct | M1 uses an extension-only classifier, dates from `doc_meta` else mtime, and `thumb_path` NULL. Full classifier rules, EXIF/filename dates and thumbnails stay in M3 | Keeps M1 to the text pipeline |
| 17 Oct | `models.py` lets tests inject a deterministic fake text embedder; only search-quality tests load bge (marked `slow`) | Fast pipeline tests without model loads |
| 17 Oct | Until M5 the parser passes text through; retrievers already accept kind/date filters | M5 only fills in the parser |
| 17 Oct | `vault reindex` added in M1 (wipe the index, re-index the known roots) | The model-mismatch message points users to it |
| 17 Oct | Path chunks are refreshed in one pass at the end of each run (compare each file's expected `path` text with the stored one; re-embed only the stale ones, in one batch), not whenever a path changes | Refreshing on every path change embedded a rename twice: once when the new path linked, again when the sweep dropped the old one. The end-of-run pass embeds once and also heals a path chunk left stale by a crash between those steps |
| 17 Oct | Zip guard targets bombs, not big files: refuse a member over 1 MB unpacked with ratio > 100, or XML parts > 50 MB; the absolute cap (`zip_max_total`) applies only to demo uploads. `.pptx` text is read from the slide/notes XML in the zip instead of python-pptx (§6.3) | Real-world test: a normal 287 MB deck with embedded video was skipped by the old 200 MB cap. python-pptx loads all media into memory: peak 288 MB vs 0.2 MB reading the XML directly, with identical text on both real decks |
| 17 Oct | `.docx` text is also read straight from `word/document.xml` (body paragraphs, then table rows), matching python-docx's text rules: tabs, text-wrapping breaks, hyperlinks, cells merged across or down repeating their text | Same reason as `.pptx`: python-docx loads all media into memory. Identical output to python-docx on the test fixtures and on both real test docx files (824 and 10 segments). Like python-docx, text in content controls (`w:sdt`) and text boxes is not read |
| 17 Oct | Embedding defaults unchanged: fastembed's quantized `Qdrant/bge-small-en-v1.5-onnx-Q`, ONNX Runtime's default thread count, batch 32 | No configuration was a clear win; see the benchmark below. The first real-world run did not re-download the model (cache dated before the run); its 83 s for 812 chunks (9.8 chunks/s) was all embedding |
| 17 Oct | **Embedding benchmark** (laptop: i5-1340P, 4 P-cores + 8 E-cores, 16 threads, Balanced power plan, on AC). 128 chunks of ~700 chars from SPEC.md, model cached, warm-up batch first, median of 3 runs, one process per config, nothing else running. Chunks/sec: **quantized, default threads, batch 32:** 15.8 / 17.4 / 10.4 / 9.1 / 9.7 / 15.9 / 10.0 across separate runs. **Threads** (quantized, batch 32; best of 2): 1 → 1.6, 2 → 11.3, 4 → 7.9, 6 → 10.7, 8 → 12.3, 12 → 10.6, 16 → 10.3. **P-cores only** (affinity to CPUs 0–7), quantized: 4 threads 11.8, 8 threads 14.0; with Windows power throttling (EcoQoS) turned off for the process: 13.4 and 14.9. **Full-precision** `BAAI/bge-small-en-v1.5` `onnx/model.onnx`: default 11.4 / 18.1 / 11.5; throttling off 17.9; P-cores 4/8 threads 10.5 / 13.6; P-cores + throttling off 14.3 / 16.4. **Batch** (quantized, default threads): 8 → 18.8 / 8.9 / 8.6 / 8.6 (interleaved with batch 32 → 9.7 / 15.9 / 10.0); 128 → 9.3 | Every config falls in the same ~9–18 band; repeated runs of one config swing between a ~9 and a ~17 chunks/s state, so laptop power/thermal state matters more than any setting tested. Embedding is compute-bound (~7 GFLOP per 160-token chunk on bge-small), so the fix for slow first indexes is two-phase indexing (open item g), not tuning |
| 17 Oct | Reading order splits lines into regions with a recursive XY-cut before §6.3's row and gap rules (vertical gutters first, then horizontal bands > 1.5× line height; bands sharing a gutter are rejoined) | §6.3 as first written grouped any vertically overlapping lines into one row, so two columns with aligned lines always interleaved and the M2 two-column test couldn't pass. Trade-off: a form with labels left and values right reads as two columns, which is harmless for search since a screenshot is usually one chunk |
| 17 Oct | OCR digit fixes: `O`/`o` + `x` + ≥ 4 hex digits → `0x`, `O` between digits → `0`. Applied to OCR text only, never to native PDF/Office text; `ocr_lines.text` matches the chunk text | Probe: RapidOCR read `0x80070005` as `Ox80070005` with Pillow's default font (16 and 24 px) and Segoe UI, the Windows dialog font (24 and 36 px), so the §12 error-code query would miss real screenshots |
| 17 Oct | A PDF image region with ≥ 20 characters of native text inside it is not OCR'd | Scans that already have a text layer would otherwise be OCR'd a second time (seconds per page) and index their text twice |
| 17 Oct | An image needing more than 15 strips is scaled down until it fits in 15 (whole image covered) rather than truncated | §6.3 set the cap but not what happens past it |
| 17 Oct | Until M3, images get an interim kind (PNG → `screenshot`, else `photo`; `kind_reason='interim:ext'`) and the mtime date. Width and height are stored now | The real classifier and EXIF/filename dates are M3; open item (i) fixes these rows without re-running OCR |
| 17 Oct | OCR uses the model files bundled in the `rapidocr` wheel, passed by explicit path | Other RapidOCR models download from ModelScope; CLAUDE.md allows only HF Hub downloads |
| 17 Oct | Images are OCR'd upright (EXIF orientation applied); stored boxes are in the upright image's pixels | Phone photos are often stored sideways with an orientation tag; the preview shows them upright |
| 17 Oct | M2 ingest speed is measured on a generated, reproducible set and labelled synthetic; the owner's real run on their test folder is added when reported | No corpus yet (§10); synthetic numbers are reproducible, real ones show real-world mix |
| 17 Oct | OCR defaults: detector long side ≤ 960 px (`Det.limit_type=max`), no text-angle classifier on screenshots (kept for photos and PDF pages), min(8, CPU count) ONNX Runtime threads, recognition batch 6. 1080p screenshot **4.5 s → 2.4 s**, word recall **97.0% → 99.9%** | See the OCR benchmark below. Skipping the classifier on screenshots also raised recall: it was flipping some upright lines |
| 17 Oct | **OCR benchmark** (same laptop as the embedding benchmark). 8 generated 1920×1080 UI screenshots (title bar, sidebar, 13–26 px body text in Pillow's default font, an error dialog; ~55 lines, ~500 words each). Model loaded and warmed up, median of 3 passes, one process per config. Word recall = share of drawn words (≥ 3 chars) found in the OCR text. **Interleaved before/after, 3 runs each:** before (RapidOCR defaults: detector short side ≥ 736, classifier on, default threads) 4.32 / 4.50 / 4.97 s, recall 97.0%; after (the defaults above) 2.36 / 2.14 / 2.71 s, recall 99.9%; after but default threads 2.52 / 2.67 / 2.87 s. **Single-run sweeps:** recognition batch 1 / 2 / 4 / 6 → 2.52 / 2.49 / 2.91 / 2.35 s, and 12 / 24 / 48 → 4.73 / 6.09 / 6.51 s (old detector); classifier off → recall 97.0% → 100% (old detector); detector long side 736 / 960 / 1536 → detect 0.26 / 0.43 / 1.03 s, recall 98.5 / 99.9 / 100%; threads 4 / 8 / default → 3.57 / 1.88 / 2.35 s; parallel engines (throughput) 2 × 8 / 3 × 5 / 4 × 4 threads → 2.11 / 1.94 / 1.75 s per image | The ≤ 1 s target was not met (open item j). Recognition is ~80% of what remains (~1.9 s), and its cost grows with the amount of text. Profiling it, the convolutional backbone is ~63% of kernel time; the 18,708-class output layer is only ~5–9%. Parallel engines add no throughput beyond one 8-thread engine on this CPU, so no multiprocessing |
| 17 Oct | **Measured ingest speed** (CLI `vault index` stage timers, tuned defaults, bge-small cached; synthetic set generated with Pillow/PyMuPDF): 27 screenshots (20 × 1920×1080, 5 × 1080×2400, 2 × 1080×6000), 10 photos (4000×3000 JPEG; 3 with a sign), 10 PDFs (5 scans × 3 pages with no text layer, 5 decks with a picture body on 2 of 4 pages); 62 images/pages OCR'd, 1,720 OCR lines. Two runs, files/sec: **screenshot 0.29 / 0.25, photo 0.59 / 0.66, pdf 0.13 / 0.15**. Stage time: OCR 170 / 177 s, embed 11 / 12 s, everything else < 5 s | OCR is > 90% of ingest time for image-heavy folders. The owner's real-world numbers (open item d) are added when reported |
| 17 Oct | `ocr_threads` defaults to min(8, `os.cpu_count()`), not a fixed 8 | 8 was best on the laptop, but RapidOCR silently ignores a count above the CPUs, so a fixed 8 on a 4-vCPU VM fell back to ONNX Runtime's default instead of 4 |
| 17 Oct | Benchmark scripts live in `bench/` (§13), with the exact commands in `bench/README.md`; generated files go to the gitignored `bench/generated/` | The §19 numbers must be reproducible, on the laptop and on the VM (open item h) |
| 17 Oct | Deploy host for M4: resize the current VM in place from B2ls_v2 to `Standard_D4as_v4` (4 vCPU, 16 GB, non-burstable) by Stop → Size → Start: same disk, IP, NSG and host key, no new VM. Then benchmark with `bench/`. Delete the resource group after 30 Oct 2026 | OCR is seconds per image and 2 burstable vCPUs can't index demo uploads in time. Azure for Students quota in India South Central: Total Regional = 6, DASv4 = 4, FALSv6 = 0, DASv5 = 0, and this subscription can't get increases at all (support's answer is Pay-As-You-Go, which we won't do), so D4as_v4 is the size that fits. Steps in `deploy/azure.md` §9 |

### Open items

Status is `open`, `done` (with the commit), or `moved to Mx` (with the reason). See CLAUDE.md for when to review them.

| ID | Item | Milestone | Status |
|---|---|---|---|
| a | Common words ("the", "is", "where") in the BM25 OR-query match nearly every chunk and add noise to RRF ranks. Decide whether to drop them when other terms remain, using the eval | M6 | open |
| b | The UI keeps one `VectorIndex` and rebuilds it after indexing; `search()` already accepts a prebuilt `text_index` | M4 | open |
| c | Date tests must set the Office `created` property explicitly: python-docx/python-pptx templates carry a 2013 creation date | M3 | open |
| d | 3 of the owner's real test PDFs have no text layer (scanned/handwritten): use them as real-world cases for scanned-page OCR. Scanned-page OCR shipped in M2; awaiting the owner's run (`vault reindex`, since unchanged files skip extraction). Handwriting is out of scope (§1, §17). Expected on the owner's set: `edited_ticket.pdf` has a text layer and is not OCR'd; the 414-page image-only book OCRs 30 pages and hits the cap; the 41-page handwritten notes OCR 30 pages with little usable text (see k); the 212-page scanned book with an OCR text layer OCRs only its ~9 blank or cover pages, not 30 (image regions under native text are skipped). The owner confirms on their run | M2 | open |
| e | The path chunk includes the file extension, so a query containing "pdf" (as in probability density) matched every PDF filename. Consider dropping the extension from the path-chunk text | M6 | open |
| f | Dense top-100 chunks can be crowded out by one long document (in the real-world test only one book appeared in the dense list). Consider retrieving more chunks before collapsing to files; measure in the eval | M6 | open |
| g | Two-phase indexing. **Phase 1** (per file, fast): list the file with its filename/path chunk (FTS) and thumbnail. **Phase 2** (background): OCR plus all embeddings. Images aren't keyword-searchable until OCR runs and OCR costs seconds per image, so OCR belongs in phase 2 with the embeddings. Dense search skips chunks without vectors; the UI shows "listed / searchable / meaning-ready" progress. Matters most for demo uploads. Due by M7 at the latest | M7 | open |
| h | At M4, resize the current VM in place from B2ls_v2 to `Standard_D4as_v4` (4 vCPU, 16 GB, non-burstable) by Stop → Size → Start: same disk, IP, NSG and host key, so no new VM. Quota in India South Central: Total Regional = 6, DASv4 = 4, FALSv6 = 0, DASv5 = 0, with no increases possible on Azure for Students. Then benchmark there with `bench/` (embedding method above; `python bench/compare_ocr.py`; the ingest-speed run; two files OCR'd in parallel) and set its OCR/embedding defaults separately if needed. The VM is needed only until 30 Oct 2026; delete the resource group after. Steps: `deploy/azure.md` §9 | M4 | open |
| i | Images indexed in M2 carry an interim kind (`kind_reason='interim:ext'`) and the mtime date. M3 must reclassify and re-date them in place (EXIF, filename, dimensions only), without re-running OCR or re-embedding | M3 | open |
| j | Time-boxed experiment (~30 min): a lighter English-only recognition model (e.g. PP-OCRv4 English mobile) from the HF Hub only (CLAUDE.md; it isn't in the rapidocr wheel). Adopt it only if `bench/bench_ocr.py` shows it ≥ 30% faster with word recall ≥ 99%, and the four M2 done-when tests still pass. Otherwise record the numbers and keep the bundled model. Context: 2.4 s per 1080p screenshot after M2 tuning vs a ≤ 1 s target, mostly recognition | M5 | open |
| k | Early stop for PDF OCR: if the first 3 OCR'd pages of a PDF give almost no confident text (e.g. handwriting), skip its remaining pages and say so in the index report. Today a 41-page handwritten PDF costs 30 pages of OCR for little usable text | M5 | open |

---

## 20. Changelog

**v2.1 (16 Oct 2026)**
- Deployment: added option D (Azure for Students VM + systemd + Caddy) and chose it (§15, §19). M0's deploy check is now "probe runs on the chosen host". Demo mode binds 127.0.0.1 behind Caddy (§2). Added `deploy/` (§13).

**v2 (16 Oct 2026)**
- Added `.docx`, `.pptx`, `.txt` and `.md` support (new kinds `doc` and `slides`), with a zip guard.
- OCR: tall images are tiled instead of shrunk; lines are sorted into reading order and grouped into blocks; chunks follow blocks; line boxes are stored in `ocr_lines`.
- PDF: embedded images covering ≥ 15% of a page are OCR'd, not just pages with almost no text.
- CLIP gate (z-score outliers, reduced weight for code-like queries) plus ablation rows for gated vs ungated.
- Content-addressed store (`files` + `paths`): duplicates collapse with "+N copies", and renames and moves cost nothing.
- Highlight boxes on image and PDF previews.
- Indexing goes newest-first, resumes after interruption, and records files/sec.
- Uploads v2: folder upload, 100 files / 200 MB caps, per-session copy of the index, scope toggle, progress, clearer notice.
- Cross-platform "Open file"; `setup.bat` / `setup.sh`; the install is tested on another machine.
- Demo plan with the offline proof (§16); limitations table (§17); Windows Snipping Tool filename dates.
- Milestones re-dated from 16 Oct and split into a core and polish, with a cut order.

---

## 21. References

Checked 14–16 Oct 2026:
- HF Spaces overview (paid plan for Gradio/Docker): https://huggingface.co/docs/hub/en/spaces-overview
- HF ZeroGPU (free-account limits, Gradio only, Python versions): https://huggingface.co/docs/hub/en/spaces-zerogpu
- fastembed supported models: https://qdrant.github.io/fastembed/examples/Supported_Models
- RapidOCR install: https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/install.en.md
- Caddy automatic HTTPS: https://caddyserver.com/docs/automatic-https · install on Ubuntu: https://caddyserver.com/docs/install#debian-ubuntu-raspbian
- Gradio `File` component (`file_count="directory"`): https://www.mintlify.com/gradio-app/gradio/components/file

Library docs: PyMuPDF https://pymupdf.readthedocs.io · python-docx https://python-docx.readthedocs.io · python-pptx https://python-pptx.readthedocs.io
