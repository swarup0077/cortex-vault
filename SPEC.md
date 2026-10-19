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
  kind_reason TEXT,                    -- e.g. 'ext:.pptx', 'filename:snipping-tool', 'exif:camera', 'png-no-exif', 'clip:screenshot', 'clip:photo'; size fallbacks while CLIP can't run: 'screen-size:1920x1080', 'tall-phone:1080x5000', 'default'
  effective_date TEXT,                 -- ISO-8601
  date_source TEXT,                    -- 'exif' | 'filename' | 'doc_meta' | 'mtime'
  width INTEGER, height INTEGER,
  pages INTEGER,                       -- PDF pages or pptx slides
  gps_lat REAL, gps_lon REAL,
  thumb_path TEXT,                     -- relative to the data dir: thumbs/<sha256>.webp
  ocr_cls INTEGER,                     -- images: 1 if their OCR ran the text-angle classifier, 0 if not (§6.2)
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
CREATE TABLE text_vectors  (chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE, vec BLOB NOT NULL); -- float32[384], L2-normalised; content chunks only (path chunks have none)
CREATE TABLE image_vectors (file_id  INTEGER PRIMARY KEY REFERENCES files(id)  ON DELETE CASCADE, vec BLOB NOT NULL); -- float32[512], L2-normalised
CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, report_json TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);  -- schema_version, model names, last_indexed
```

- **One transaction per file**, covering its files row, chunks, FTS rows, OCR lines and vectors. An interrupted run therefore loses at most the file in progress, and the next run resumes through the fast path. FTS5 has no foreign-key cascade, so FTS rows are deleted explicitly.
- `meta` stores the embedding model names. If they don't match the configured models at startup, refuse to search and tell the user to run `vault reindex`.
- The demo index (`demo_index/`, built by `vault build-demo`) stores paths relative to the repo (`corpus/screens/x.png`), resolved against the repo root at run time: the laptop's absolute paths don't exist on the server, and the public UI shouldn't show them (§19, 18 Oct).
- Every file has one `source='path'` chunk listing the filenames (without the extension) and parent folder names of **all** its paths, e.g. `Goa trip 2025 / IMG_2041`. Folder names carry real signal, and this gives image-only files something for BM25 to match. The path chunk is **keyword-only**: it has an FTS row but no vector, so it never shows up as a weak dense match, and a rename, move or copy embeds nothing (§19, 17 Oct). Its text is rewritten at the end of any run that changed the file's paths.
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
   - **Two phases (open item g, §19 18 Oct):** phase 1 lists every new file: kind, date, native text (PDF text layer, Word, PowerPoint, plain text), thumbnail and the path chunk, keyword-searchable at once (`files.stage = 'listed'`). Phase 2 then reads each listed file, newest first: CLIP (which also decides an undecided image's kind, §6.2), OCR of images and of scanned or picture-heavy PDF pages, and the text vectors (`stage = 'ready'`). Each phase writes a file in one transaction. A run stopped in phase 2 leaves files listed, and the next run finishes them. The demo shows the per-file upload outcome after phase 1 and "Reading X of Y uploads" during phase 2; searches meanwhile find listed files by name and native text, and by meaning and pictures as phase 2 reads them.
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
  4. Anything else (a JPEG, WebP or HEIC with no camera EXIF): **CLIP decides**, from the image vector the file gets anyway (§6.5). If the image's cosine to "a screenshot of a computer or phone screen" leads its cosine to "a photograph" by at least `clip_screenshot_margin` (0.04, provisional; §19) → `screenshot` (`clip:screenshot`), otherwise `photo` (`clip:photo`). Size can't decide this: a forwarded photo has no camera EXIF and often a screen-like size (WhatsApp makes a 20:9 photo 720×1600), §19, 18 Oct.

  **Fallback** while CLIP can't run (e.g. no model offline): the old size rules give a temporary kind, and the end of a later run decides with CLIP once the file has a vector.
  - No EXIF and dimensions matching a common screen resolution (desktop and phone, either orientation; the list is in `classify.py`) → `screenshot`.
  - A phone-width image (720, 1080, 1170, 1179, 1242, 1284 or 1440 px wide) at least twice as tall as it is wide → `screenshot`.
  - Otherwise → `photo`.
- **Text-angle classifier:** only screenshots found by rule 1 or 3 skip it, since on-screen text is never upside down. Every other image keeps it, because a CLIP or size-rule "screenshot" may be a forwarded photo. `files.ocr_cls` records the setting each image's OCR used.
- **End of every run:** images no rule decided are relabelled from their stored CLIP vectors (nothing is decoded). An image whose stored `ocr_cls` differs from what its kind reason now calls for is OCR'd again, in one transaction. A kind change alone isn't, since the kind reaches OCR only through that setting.

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
2. a date in the filename (the screenshot patterns above, `IMG_20250312_…`, `PXL_2025…`, and WhatsApp's two forms: `IMG-20250312-WA0001.jpg` with `VID-`/`AUD-`/`DOC-` alike, and the desktop app's `WhatsApp Image 2025-12-23 at 09.28.46.jpeg`);
3. document metadata (`doc_meta`): PDF `creationDate`, or Office core property `created`;
4. file mtime.

WhatsApp strips EXIF, but its filenames keep the date. Files with neither (for example `Screenshot (12).png`) fall back to mtime.

**GPS:** read from EXIF if present. It is stored only; it's used in a stretch goal.

**Thumbnails:** 320 px on the long side, WEBP, at `data/thumbs/<sha256>.webp`.
- Images: the image itself.
- PDFs: page 1.
- `.docx`, `.pptx`, `.txt`, `.md`: a generated card (file-type badge plus the title or first line), since rendering Office files would need LibreOffice.
- When a file leaves the index (last path gone, content changed, `reindex`), its thumbnail is deleted from disk. Demo uploads' thumbnails live in the session directory and go with it at expiry.

### 6.4 Chunk
- **PDF pages and pptx slides:** one chunk per page or slide. If it's over 1,200 chars, split it into ~800-char windows with 150-char overlap, keeping the page number.
- **docx / txt / md:** ~800-char chunks cut on paragraph boundaries, with 150-char overlap.
- **OCR text:** chunks are built from whole layout blocks, up to ~800 chars. A block is never split unless it alone exceeds 1,200 chars. A typical screenshot ends up as one chunk.
- Normalise whitespace and keep the original casing; FTS5 handles case-folding.

### 6.5 Embed
- Text chunks go to bge-small in batches of 32. CLIP-vision sees every image file, screenshots included, but not PDF pages: a new image is embedded on its own, inside its file's transaction (§5), from the upright image already decoded for OCR, shrunk to a 448 px short side; the end-of-run backfill (§19) embeds in batches of 16.
- L2-normalise everything before storing.
- Time each stage (§6.1, step 7).

---

## 7. Search

### 7.1 Query parsing (`search/parse.py`, rule-based, deterministic)
Input: the raw query and `now` (injectable, for tests). Output: `ParsedQuery(text, kinds, date_from, date_to, phrases)`.

**Build order (§19, 17 Oct):** M4 builds only the kind words *screenshot(s)*, *photo(s)/picture(s)*, *pdf(s)*, *slide(s)/deck/ppt* and *doc(s)*, with the mappings in the table below, sharing code with the UI's kind chips. *notes* is left out (§19, 18 Oct): it's a content noun as often as a kind word ("handwritten notes", "sticky notes", "release notes"), and as a pdf + doc filter it would drop every image from such a query. The rest of the table, dates, glue-word stripping and removable parsed-filter chips are Later (§14). Kind chips and kind words combine as a union.

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
| BM25 | `bm25(chunks_fts)` over chunks; query = content tokens (OR; stopwords dropped while another token remains) plus required phrases (AND) | 100 chunks |
| All terms | the same, but every content token is required (AND), so only chunks holding the whole query match. Counts as BM25 in the ablations (§9) | 100 chunks |
| Dense text | cosine(bge(text), text_vectors): content chunks only, never path chunks. Chunks below `dense_min_cosine` (0.42, provisional; §19) are dropped at any collection size | ≤ 100 chunks |
| CLIP | cosine(clip_text(text), image_vectors), then the gate (§7.3) | ≤ 50 files |

`vectors.py` provides `VectorIndex`. It loads a vector table into a NumPy matrix with a row→id map, runs exact top-k by matmul with `argpartition`, and supports a filter mask. Rebuild it after indexing.

### 7.3 CLIP gate (`search/gate.py`)
**The problem:** CLIP returns a full ranking for every query, even `error 0x80070005`. In RRF, CLIP's rank 1 counts exactly as much as BM25's rank 1, so a random photo can tie with the correct screenshot.

**The gate:**
- Compute z = (score − mean) / std over all filter-passing candidate images, and keep only hits with **z ≥ 2.0** (configurable), at most 50.
- If there are fewer than 20 candidates, keep the top 5 instead, but only those with cosine ≥ `clip_min_cosine` (0.26, provisional; §19). Without it, a small collection lifted up to 5 unrelated images into every query.
- If the query contains a quoted phrase or a code-like token, multiply CLIP's RRF weight by 0.5 (configurable). A code-like token is either a hex value like `0x80070005`, a run of 6 or more digits, or a token of 4 or more characters mixing letters and digits.
- The gate also marks **strong** hits for the visual-query rule (§7.4): z ≥ `clip_strong_z` (3.25), or, with fewer than 20 candidates, cosine ≥ `clip_strong_cosine` (0.28). Both provisional (§19).
- The gate logic is unit-tested on synthetic score arrays. The eval compares gated vs ungated (§9).

### 7.4 Fusion
1. **Collapse to file:** for each retriever, keep each file's best-ranked chunk. That rank is the file's rank in that list, and the chunk is remembered as the snippet candidate.
2. **Reciprocal Rank Fusion:** `score(f) = Σ_r w_r / (k + rank_r(f))`, with `k = 60` and `w_r = 1.0` by default, adjusted by the gate. A file missing from a list contributes 0 for that list. A weight of 0 leaves a list out. The all-terms list exists because ranks alone can't separate a file that holds the rare exact word (BM25 #1, crowded out of dense) from a long document that both lists like a little (BM25 #2 + dense #1): see §19, 17 Oct.
   - **Visual-query rule (§19, 18 Oct):** when the all-terms list is empty (no file holds every content word), the query is neither quoted nor code-like, and CLIP has at least one strong hit (§7.3), BM25's weight is multiplied by 0.5 and each strong CLIP hit's by 2; weaker gated hits keep the normal weight. Without it, a file matching one or two of the words through BM25 and dense (2/61) always outranked CLIP's #1 (1/61); now CLIP's strong #1 (2/61) beats any file that only BM25 #1 and dense #1 found (0.5/61 + 1/61). The factors are provisional.
3. **Snippet:** the best chunk from the all-terms list if present, else from BM25, else from dense. Query terms are highlighted with `<mark>` in a ~200-char window. Image-only hits get no snippet and a "visual match" badge.
4. **Weaker matches (§19, 18 Oct):** the floors and the gate decide what enters fusion, but they never cut the list short. Below the fused list, under a "Weaker matches" divider, come the filter-passing files that only the dense floor or the CLIP gate dropped, ranked by RRF over dense without its floor and CLIP without its gate, up to the result cap. They carry a "weaker match" badge. Kind filters and the upload scope still apply, so a filter that leaves one file gives one result.

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

## 8. UI: Cortex Vault (Gradio Blocks)

The UI is called **Cortex Vault**: the page title, header, favicon and footer say so. The package, CLI and this spec keep `vault` / Personal Search Vault. The approved design is the pack in `design/` (README, `reference.html`, `cortex.css`, screenshots; committed for reference, not served). This section records what the app does; `design/README.md` has the visual detail (§19, 18 Oct).

**One screen, no tabs.** Header (brand · status · scope control in demo mode · the one primary button), search bar, file-type chips, results on the left with a sticky 440 px preview on the right, footer (mode line and key hints).

**Search**
- Runs as you type: about 300 ms after the last key, only when the box holds at least 2 characters and differs from the last search; only the newest request counts (`trigger_mode="always_last"`). "N results · T s" next to the box comes from the app. If the VM's query time (stage timers) goes above ~0.5 s, the delay goes up.
- **Enter** opens the selected result, but only if the results on screen belong to the text now in the box; otherwise it runs that search at once and opens nothing.
- **File-type chips** (All, Screenshots, Photos, PDFs, Slides, Docs) filter the results on the page (up to 50); their counts come from the full result set, and groups left empty are hidden. Kind *words* in the query stay hard filters (§7.1). A chip is not a new search: with "PDFs" on, you see the PDFs among the top 50.
- **Keyboard (combobox pattern):** focus stays in the search box; ↑/↓ moves the selection (`aria-activedescendant`) in display order and updates the preview; Enter opens; Esc closes the phone sheet if it's open, otherwise clears the box; Ctrl K or `/` focuses search. No Ctrl C / D / L shortcuts.

**Results, grouped (presentation only; the ranking is unchanged, §7.4)**

| Group | What goes in it |
|---|---|
| Close matches | results above the weaker-matches divider that some text method found |
| Visual matches · found by what is in the picture | results above the divider that only CLIP found, as thumbnails |
| Related matches | the weaker matches (§7.4 step 4) |

- Close and Visual matches appear in the order of their best item's fused rank, so the #1 result is always at the top of the page; Related matches always come last. Inside a group the order is the fused rank.
- The top 20 results by fused rank show, each in its own group (weaker matches rank after every fused result), then "Show N more" up to 50, N being the real remaining count under the current chip. A visual match at #2 therefore shows even when a long Close group comes before its group.
- A row: tinted file-type tile, file name, meta (type · date, or page / slide number), one-line snippet with `<mark>`, "Enter" hint on the selected row.

**Preview pane** (a full-screen sheet on phones)
- The image, the rendered PDF page, or the thumbnail card of a document.
- **Highlight boxes** in percent of the picture: on OCR lines (images, OCR'd PDF pages and regions) and on the PDF's own text layer (`page.search_for` at render time). Query words shorter than 3 characters and stopwords are ignored; at most 10 boxes per page. A meaning-only match gets one lighter box around the best passage's lines instead (OCR text only: a native text layer has no passage boxes).
- Title and meta line.
- **Found by:** for every result, each search method that returned it with its real rank in that method's list: exact words (BM25), every word (the all-terms list), meaning (dense), what is in the picture (CLIP). Related matches show the rank in the method's full list, below the dense floor or outside the CLIP gate. **Details** (closed by default, both modes): each method's RRF share, the total, and the CLIP gate result, the same numbers as `vault search --debug`. This replaces the "show scores" toggle.
- **Text read by:** OCR ("on this server" in demo mode, "on this computer" locally), the PDF's own text layer, Word document text, slide text, plain text, or no text found.
- **Size:** pixels or pages / slides, and bytes.
- **Actions:** Download (demo, `/file/{id}`) or Open (local, `vault/platform.py`) · Copy text (the matched passage) · Copy link to this result (`?q=…&r=<id>`; never a path).

**Home:** centred column, 760 px. "Search N files by words, meaning, or what is in a picture", a 56 px search box with a Ctrl K hint, "Try a search" (each with its search type) and "Recently added" (newest indexed first), then in demo mode the drop zone, the upload notice and the note about the three models, and in local mode the indexed folders.

**States:** no results (a one-line suggestion); still reading uploads ("Reading X of Y uploads" in the header, a thin banner with a progress bar, search keeps working); after an upload, each file listed as added, already in your uploads (sha256), not a supported type, or couldn't be read, with the reason.

**Demo mode**
- **Add files** (header) and the home drop zone upload files; they replace the Upload tab. Folder upload is Later (§14).
- **Limits:** ≤ 100 files, ≤ 25 MB each, ≤ 200 MB total per session, with an extension allow-list.
  - Enforce the limits in our own code, not only through Gradio's settings.
  - Final (M5, §19 18 Oct): a mixed 100-file upload is listed in 8 s and fully read in 3.5 min on the deploy host, inside the ≤ 5 min target, so these caps stay.
- **Session index:** on the first upload, copy the demo index (SQLite backup API) into `/tmp/vault_sessions/<uuid4>/` and index the uploads into that copy. The demo index itself never changes.
- **Incremental (§19, 18 Oct):** every later upload adds only its new files to the session index; a file identical (sha256) to one already in the session is skipped. Uploading 30 files and then 20 more indexes 20 the second time, and the report says "added 20".
- Uploads are indexed one job at a time (a queue): OCR uses every CPU, and the OCR engine keeps per-call state.
- **Scope control:** *Samples*, *My uploads* or *Both*; *Both* before any upload, *My uploads* once something is uploaded. Hidden on phones, where the scope is always *Both*.
- Wipe a session after 30 min idle, and wipe all sessions on app start.
- **At most `max_sessions` = 20 upload sessions at once** (§19, 18 Oct). With all 20 open, a visitor without a session sees "The demo is busy, try again in a few minutes" in place of the drop zone, Add files shows the same instead of opening the file picker, and an upload that arrives anyway is turned away whole (nothing kept, Gradio's copies included). Searching the sample files keeps working for everyone, and a visitor who has a session can still add to it.
- Visible notice, under the drop zone and above the upload outcome: "Uploads are processed on this server, kept only for your session, and deleted after 30 minutes. For private use, run it on your own computer (see README)." The footer says "Demo server · your uploads are deleted after 30 minutes".
- `/file/{id}` sends a file as an attachment with its content type, by DB id (plus the session id for uploads). An unknown id, another session's file and anything path-like all get 404.

**Local mode:** the same design without the scope control; "on this computer" in the footer and in "Text read by"; Enter opens the file through `vault/platform.py`. The primary button is **Folders**: a panel to add a folder, remove one, and "Index now" with progress, the last run report, counts by kind and the error list (it replaces the Index tab; automatic re-indexing is Later, §14). The panel and its handlers exist only in local mode. The status reads "N files · indexed <when>".

**How it works** (M6): architecture diagram, the eval ablation table (from `eval/results.md`), a privacy note, and a link to "Run on your own files".

**Files and look**
- Thumbnails, previews and downloads are served only by file id or session id, through the app's own routes (`/thumb/{id}`, `/preview/{id}`, `/file/{id}`), which look the file up in the DB. No URL carries a path, and Gradio's `allowed_paths` stays empty (§19, 18 Oct).
- Static assets (`cortex.css`, `cortex.js`, the Geist fonts with their OFL licence, the logo) are served by the app from `vault/ui/static/` at `/cv/…`. Nothing loads from the internet: the Gradio theme names the fonts as plain strings, so no Google Fonts.
- Dark by default; light through `?__theme=light`. One primary button per screen; brass only for focus, highlight boxes and the logo; no shadows or gradients.
- Every file name, path, snippet and OCR string is HTML-escaped on the server; the only tags our code adds are `<mark>` and the layout.

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
  - `filtered`: kind words (M4); date words are Later (§14), so date-based queries are written now but marked Later and skipped until the date parser is picked;
  - `mixed`.
- **At least 40 queries, at least 6 per category.** The set must also include:
  - ≥ 6 queries whose targets are `.docx` / `.pptx` files;
  - ≥ 3 targeting text in tall screenshots;
  - ≥ 3 targeting text inside images on PDF pages.

  Write the queries *before* looking at results.
- Relative-date queries (Later, with the date parser) run with a **fixed `now`** set in the eval config, so results don't drift with the calendar.
- **Metrics:** Recall@1, Recall@5, Recall@10 and MRR@10, overall and per category. Also query latency p50/p95.
- **Ablations** (one row each). "BM25" includes the all-terms list (§19, 17 Oct):
  1. BM25 only
  2. dense only
  3. CLIP only
  4. BM25 + dense
  5. all three, CLIP ungated
  6. all three, CLIP gated
  7. all three, CLIP gated, without the all-terms list (measures its effect)
  8. all three, gated + rerank (only once rerank is built; it's Later, §14)
- **Tuning (§19, 17 Oct):** no threshold or weight is ever chosen from the owner's personal test files.
  - Every value is first calibrated on generated data with a script in `bench/` and marked provisional.
  - Each query in `eval/queries.jsonl` carries a fixed `"split": "tune"` or `"heldout"`, about half each and balanced per category. M6 tunes the provisional values on the `tune` half only, and the README reports the `heldout` half.
  - Every fix targets a general cause and comes with a test on generated files.
- **Output:** `eval/results.md` (a table plus a timestamp, corpus size and model names) and `eval/results.json`.
- **Expected story:**
  - BM25 wins `exact`, dense wins `semantic`, CLIP wins `visual`.
  - The all-terms list lifts `exact` and `mixed` (row 6 vs 7).
  - The gate lifts `exact` without hurting `visual`.
  - The full system is best or near-best everywhere.

  Report what actually happens, failures included.

---

## 10. Demo corpus (`corpus/`, committed, non-personal only)

**The 7 demo scenarios.** The corpus covers each one with public or self-made files, never personal ones:
1. **Which deck had that example?** Several versions of a lecture deck; a query finds the right slide.
2. **That paper about…** PDFs with meaningless names (arXiv numbers); a query worded differently from the paper finds the right page.
3. **The error screenshot.** Error dialogs and tracebacks; an exact code or message finds the right one.
4. **The photographed notice.** Phone photos of printed notices and whiteboards, one of them upside down; OCR finds them.
5. **Tickets and receipts.** Exact times, dates or amounts find them.
6. **Photos by what's in them.** Photos with no text; CLIP finds them ("dice on a table", "a cat on a sofa").
7. **Scanned books and notes.** Image-only scanned pages, plus a book whose contents page competes with the section page (open item n).

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
| `screenshots from last week` (fixed `now`; **Later**, needs the date parser; skipped until then) | only screenshots in that date range, newest first |
| `"<an exact sentence copied from one corpus PDF>"` | that PDF and page (exact-phrase path) |

`tests/test_smoke.py` runs these against the demo index. It's skipped if the index is missing.

---

## 13. Repo layout

```
personal-search-vault/
  CLAUDE.md  SPEC.md  README.md  requirements.txt  .gitignore
  design/                        # the approved Cortex Vault UI design pack (reference only, not served)
  setup.bat  setup.sh            # check Python 3.12, create venv, install, launch
  app.py                         # Gradio entry; reads VAULT_MODE
  deploy/                        # azure.md runbook, systemd unit, Caddyfile (§15)
  bench/                         # bench_embed.py bench_ocr.py compare_ocr.py make_bench_corpus.py calibrate.py scenes.py
                                 #   README.md: reproduce the §19 numbers
                                 #   (output in bench/generated/, gitignored)
  vault/
    __main__.py                  # CLI: index | reindex | search | stats | eval | scrub-exif | build-demo
    config.py                    # dataclass config + env overrides; data dir, roots, weights, gate, limits
    db.py                        # schema, migrations, transactions, FTS sync
    models.py                    # lazy singletons: text_embedder, clip_text, clip_vision, ocr, reranker
    vectors.py                   # VectorIndex
    platform.py                  # OS-specific bits: open file, default folders
    ingest/  discover.py classify.py meta.py (EXIF, GPS, dates) extract_pdf.py extract_office.py extract_image.py
             ocr.py (tiling, reading order, blocks) chunk.py thumbs.py pipeline.py
    search/  parse.py retrievers.py gate.py fuse.py rerank.py snippet.py highlight.py engine.py
    library.py                   # one index as the app sees it: cached vectors, files by id, detail, stats
    demo.py                      # build-demo (repo-relative paths, GPS guard) and scrub-exif
    session.py                   # demo-mode per-session index copy, upload limits, TTL cleanup
    eval.py
    ui/      layout.py views.py (HTML builders) server.py (id-only /thumb, /preview and /file routes)
             static/ cortex.css cortex.js fonts/ (Geist + OFL.txt) logo/   (served at /cv/)
  corpus/   (+ MANIFEST.csv, make_standin.py)   demo_index/ (vault.db + thumbs, built by `vault build-demo`)
  eval/     queries.jsonl results.md results.json
  tests/    conftest.py (generates fixtures) test_*.py
  data/     (gitignored: local vault.db, thumbs, model cache)
```

---

## 14. Milestones and acceptance criteria

The plan has one Claude Code session per milestone, with a commit at the end of each. This is one of four viva projects, so the plan front-loads a **submission-safe core** (M0–M4: working search, deployed). After that, **anything that makes the demo faster or more attractive comes first** (§19, 17 Oct): M5–M6 are done as time allows, trimmed using the cut list below if needed, M7 is a buffer, and the items under "Later" are picked one at a time, only if the owner chooses them.

### Core (must ship)

| # | Date | Milestone | Done when |
|---|---|---|---|
| M0 | Fri 16 Oct | **Scaffold + deploy probe** | Repo, git, venv on Py 3.12, `requirements.txt`, `pytest` green on an empty suite. `probe/app.py` is a hello-world Gradio app that loads bge-small via fastembed and embeds one query; the probe runs on the chosen host (§15). The deploy path is recorded in §19. |
| M1 | Sat 17 Oct | **Text pipeline** | Content-addressed discover (fast path, hashing, linking renames and duplicates, newest-first, sweep, resumable). PDF native text; docx/pptx/txt/md with the zip guard; chunking; FTS5 + bge; RRF over 2 lists; CLI `index`, `search`, `stats`. **Tests:** a PDF fixture returns the right **page**, and a pptx fixture the right **slide**. A docx fixture is found by paraphrase. Copying a file adds a path but no chunks. A rename re-embeds no content chunks (only the file's one `path` chunk, §5). Deleting the last path removes all rows. |
| M2 | Sun 18 Oct | **OCR** | RapidOCR on all images: tiling, reading order, blocks, block-aware chunks, `ocr_lines`. PDF scanned-page and image-region OCR. **Tests:** a Pillow-drawn `ERROR 0x80070005` is the top hit for `0x80070005`. Text in the bottom strip of a 1080×6000 fixture is found, and its box is in the right place. A two-column fixture gives two blocks rather than interleaved lines. Text drawn inside an embedded image on a PDF page is found. **Measured** files/sec per kind recorded in §19. |
| M3 | Mon 19 Oct | **Photos, CLIP, metadata** | CLIP vision/text as the 3rd list, with the gate (unit-tested on synthetic scores). Classifier with a test per rule, including Snipping Tool names. Date extraction with a test per source. Thumbnails for all five kinds. |
| M4 | Tue 20 Oct | **UI + first deploy (submission-safe checkpoint)** | Search tab with a ranked list (top 20 cards + "Show more" up to 50; CLI default `-n 10`; the gate and filters never cut the list to one result), thumbnails, snippets, kind chips, and the kind words in the query (§7.1 subset, sharing code with the chips). Index tab with a manual "Index now" (local mode only). Cross-platform "Open file". `VAULT_MODE=demo` with a read-only demo index built from a ~50-file first slice of the corpus. Multi-file upload into a session copy of the index, with a scope toggle (*My uploads* / *Sample files* / *Both*); upload limits enforced in code; the 30-minute expiry. VM resized in place to `Standard_D4as_v4` and benchmarked (open item h). Deployed, and the URL works from a phone. **After M4 plus transcripts, the submission is complete.** |

### Demo first (target dates are flexible; the other viva projects may come first)

| # | Date | Milestone | Done when |
|---|---|---|---|
| M5 | Wed 21 Oct | **Speed + visual polish** | UI redesign to `design/` (Cortex Vault, §8). Two-phase indexing (open item g): filename and native text searchable within seconds; OCR, embeddings and CLIP run in the background, with progress shown in the UI. Highlight boxes. "Why this result?". Open items (j) and (k). A 100-file upload is timed on the VM and the final caps are set. |
| M6 | Thu 22 Oct | **Full corpus + eval** | ~200 files covering the 7 demo scenarios, plus MANIFEST. ≥ 40 queries meeting the §9 coverage. `vault eval` writes the ablation table (§9) and latency, with laptop vs VM numbers. Open item (n) first, then (o) and (f). Demo index rebuilt and redeployed. |
| M7 | Fri 23 Oct | **Buffer** | Finish anything left from M4–M6. Then optional items from "Later", only the ones the owner picks. |
| M8 | Sat 24 Oct | **Pack** | README: the problem, a GIF, architecture, the §11 decisions, the eval table, measured numbers, §17 limitations, and "Run on your own files". Offline demo rehearsed (§16). |
| — | Sun 25 – Mon 26 Oct | Buffer | Fix whatever broke. No stretch goals unless M0–M8 are done. |
| — | **Tue 27 Oct, before 2 PM** | Submit | URL + transcript HTML on MS Teams. |

**Later** (optional, decided at the time; M7 is the earliest slot):
- the rest of the query parser (§7.1): date filters, the other kind words, parsed-filter chips;
- rerank (§7.5);
- the "+N copies" duplicate badge;
- folder upload;
- automatic re-indexing: a file watcher (`watchdog`, ~2 s debounce, because the OS fires several events per save) or a periodic scan. The manual "Index now" button is in M4;
- `setup.bat` / `setup.sh`, with the install tested on a different computer.

**Cut order if behind** (drop from the top):
1. Open items (j) and (k).
2. "Why this result?". Keep the ranks in `vault search --debug`.
3. Highlight boxes. Keep text snippets; `ocr_lines` is still stored, since that's cheap.
4. Eval rows beyond 1–3 and the full system.

**Never cut:** a working deploy, the transcripts, and a README with honest limitations.

**Stretch goals** (only after M8):
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
- Ubuntu 24.04, India South Central, user `azureuser`. Created as `Standard_B2ls_v2` (2 vCPU, 4 GiB RAM); resized in place on 18 Oct (M4) to `Standard_D4as_v4` (4 vCPU, 16 GB, non-burstable, AMD EPYC 7763) by Stop → Size → Start, keeping its disk, IP, NSG and host key (open item h, `deploy/azure.md` §9). The VM is needed only until 30 Oct 2026.
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
- Handwriting: neat handwriting is partly readable; the rest gives little usable text (owner's real-file test, §19).
- Screenshot vs photo for images with no screenshot name, camera EXIF or PNG (e.g. WhatsApp-forwarded): CLIP decides, and photographed notices can come out as screenshots. Their text is still read the same way.

**Doesn't work**
- Non-English text: the models are English-only, and the default OCR models read English and Chinese.
- Specific people, since there's no face recognition.
- Place names, except famous landmarks. GPS lookup is a stretch goal.
- Counting and negation ("exactly 3 people", "without the sidebar").
- Password-protected PDFs, pictures inside Word/PowerPoint files, and `.doc`, `.ppt`, `.xlsx`, video, audio, email and archives.

---

## 18. Security and privacy checklist

- [x] Local mode binds `127.0.0.1`; only demo mode is exposed. (`app.py` binds 127.0.0.1 in both modes.)
- [x] On the VM, Gradio binds `127.0.0.1:7860`; only Caddy (80/443) and SSH (22, key-only) are reachable. (18 Oct: `ss -tlnp` lists 22/80/443 public, 7860 and Caddy's 2019 on loopback; `sshd -T` has password and keyboard-interactive auth off; 7860 refused from the laptop.)
- [x] Demo mode resolves every served file from a DB id or a session id. No path parameters. (`/thumb/{id}`, `/preview/{id}`, `?s=<uuid4>`; `tests/test_ui.py`.)
- [x] Session ids are `uuid4`; session dirs live under one root and are wiped on TTL and on start. (`vault/session.py`; `tests/test_session.py`.)
- [x] Uploads: extension allow-list; size, count and total limits enforced in code; zip guard for `.docx` / `.pptx`; every decode wrapped in try/except. (Plus: only files inside Gradio's upload folder are read.)
- [x] The upload notice says plainly that uploads are processed on the server.
- [x] Nothing personal in git: no personal files, no GPS EXIF, no model cache, no `data/` (`.gitignore` covers them). (The M4 corpus is generated; `build-demo` refuses images with GPS.)
- [x] No telemetry. The only network calls are model downloads, and none at all with `HF_HUB_OFFLINE=1`. (`app.py` sets `GRADIO_ANALYTICS_ENABLED=False` and `HF_HUB_DISABLE_TELEMETRY=1` before Gradio loads; the theme uses system fonts, so the page loads nothing from Google Fonts.)

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
| 17 Oct | **Owner's real-file M2 test:** 43 queries, 39 behaved as expected. Every PDF expectation in open item (d) held: Kozen (414-page image-only book) and the structures notes (41-page handwritten) hit the 30-page OCR cap, the green book (212-page scan with a text layer) OCR'd only its cover and blank pages, and `edited_ticket.pdf` had no OCR. Neat handwriting is partly readable, so §17 now lists handwriting under "partly works". Found: an upside-down photo's text was missed, icons were read as lone CJK characters, RapidOCR warned once per text-free image, and four ranking patterns (open item l) | Real-world check of M2 before M3 |
| 17 Oct | The OCR engine passes `use_cls` as a bool on every call (`None` → `ocr_use_cls`) | RapidOCR stores a per-call `use_cls` on the instance and reads `None` as "keep the last value". Since screenshots started skipping the classifier (940772c), every photo and PDF page OCR'd after the first screenshot in a process ran without it, so a notebook cover photographed upside down came out as garbage. Reproduced with a generated cover: classifier on → all 3 lines read, every crop classified `180` at 1.0; after one screenshot → "Llec ok", "Sehee", "DA ES". Fixed in dc60345. The M2 ingest numbers for photos and PDFs were measured with this bug (re-measured below) |
| 17 Oct | OCR text cleanup: in a line with more Latin letters than CJK characters, a CJK ideograph with no CJK neighbour is dropped (`品 Settings` → `Settings`). Runs of two or more, and lines with no Latin text, are kept. `ocr_lines` gets the same text | The bundled Chinese-English recogniser reads UI icons as single ideographs (品 白 这 米), which showed up in snippets of the owner's screenshots. Non-English text is out of scope (§1) |
| 17 Oct | RapidOCR logs at ERROR. The index report counts images and PDF pages that were OCR'd but gave no text (`ocr_empty`, shown as "of which no text") | It warned "The text detection result is empty" for every photo or blank page without text. A count is useful; the noise wasn't |
| 17 Oct | `PRAGMA secure_delete = ON` on every connection, plus FTS5's own `secure-delete` option on `chunks_fts` (SQLite ≥ 3.44; stored in the table and written once; older SQLite logs a warning) | So text from deleted files, demo uploads included, doesn't linger in the DB file. The pragma alone isn't enough: FTS5 keeps a deleted row's stemmed terms in its index blobs until a segment merge rewrites them (measured: a merged 2,000-row index kept them with no later writes; both settings → zero copies). The laptop venv has SQLite 3.53.1. DB files written before this keep older leftovers until a one-time FTS `optimize` and `VACUUM` |
| 17 Oct | **Ingest speed re-measured** with the classifier fix (same synthetic set, method and laptop as the M2 measurement; photos and PDF pages now really run the classifier). Two runs, files/sec: **screenshot 0.30 / 0.30, photo 0.62 / 0.60, pdf 0.15 / 0.13**. OCR stage 160 / 170 s, embed 9 / 11 s; 62 images/pages OCR'd, 0 without text | Same band as the M2 numbers (0.29 / 0.25, 0.59 / 0.66, 0.13 / 0.15; OCR 170 / 177 s), so the classifier's cost on photos and PDF pages is within run-to-run noise |
| 17 Oct | **Ranking fixes agreed** (open item l): in M3, in this order, (d) BM25 drops stopwords when a content term remains, (a) an "all terms" lexical list (FTS5 AND of the content terms, ranked by bm25) fused as one more RRF list, (b) path chunks keyword-only with (e). In M6, first and before any weight/k tuning, (c) table-of-contents pages (open item n); the owner checks its threshold on their real books | Proposals from the owner's real-file test. Weights alone can't fix (a): a file at BM25 #1 and missing from dense scores 1/61, one at BM25 #3 and dense #1 scores 1/63 + 1/61, so BM25 would need over 31× dense's weight |
| 17 Oct | **Path chunks become keyword-only** (FTS, no vector, never in dense), and their text drops the file extension. This reverses the 17 Oct decision that kept folder names in dense search; a rename or copy then embeds nothing. §5 and §7 change when it lands in M3 | Path-only chunks ("folder / filename") matched unrelated queries as weak dense hits in the owner's real-file test. The extension made "pdf" match every PDF filename (open item e) |
| 17 Oct | OCR lines that are only one or two CJK characters (spaces and punctuation aside) are dropped when the rest of the image is mostly Latin: at least `ocr_latin_share` = 0.8 of its letters. Runs of 3+ CJK characters, and mostly CJK images, are kept | Completes the stray-CJK cleanup: an icon detected on its own (`品`, `白 这`) is a line by itself, which the within-line rule can't see (open item m). 0.8 is a first value, not measured |
| 17 Oct | `vault reindex` ends with an FTS `optimize` and `VACUUM` (`db.compact`), and so does any index run that deleted files. A busy DB logs a warning instead of failing the run; the time is the report's `compact` stage | Secure delete only covers deletes from now on. Compacting removes what older deletes left in the FTS index and free pages, so the owner's existing index is clean after one `reindex` |
| 17 Oct | **§14 reordered, demo first.** M4 gains the ranked list (top 20 + "Show more" to 50; the gate and filters never cut it to one result), the scope toggle, upload limits in code, the 30-minute expiry and the VM resize + benchmark (h). M5 becomes speed + visual polish: two-phase indexing (g, moved from M7), highlight boxes, "Why this result?", (j), (k), the 100-file upload timing and caps. M6 keeps the corpus and eval, with (n), (o), (f) and laptop vs VM numbers. M7 becomes a buffer. The parser, rerank, "+N copies", folder upload, a file watcher and the setup scripts move to "Later", picked one at a time | Agreed priority: anything that makes the demo faster or more attractive comes first. Until the parser is picked, `parse()` stays a pass-through (17 Oct row above); open items p and q follow from this |
| 17 Oct | M3 scope: (1) classifier rule 5: a phone-width image (720/1080/1170/1179/1242/1284/1440 px) at least twice as tall as wide, with no camera EXIF, is a screenshot; camera EXIF always wins. (2) WhatsApp filename dates in both forms (`IMG-YYYYMMDD-WA####`, also `VID-`/`AUD-`/`DOC-`, and `WhatsApp Image YYYY-MM-DD at HH.MM.SS`). (3) CLIP on every image file, screenshots included, not on PDF pages. (4) A file's thumbnail is deleted from disk when the file leaves the index; demo thumbnails go with the session. (5) The all-terms list counts as BM25 in the ablations, plus a row without it. (6) Classifier and dates stay in M3 | Owner's answers to the M3 plan. (1) catches scrolling screenshots that lost their filename without misfiling 20:9 phone photos. (2) WhatsApp strips EXIF but keeps the date in the name. (4) matches secure delete: nothing of a removed file stays on disk |
| 17 Oct | CLIP (M3): fastembed `Qdrant/clip-ViT-B-32-vision` + `-text`, lazy singletons; `meta.clip_model` records both names and a mismatch refuses to index or search. A new image is embedded inside its file's transaction; a CLIP failure (e.g. no model offline) keeps the file and leaves the vector to the next run's backfill. The CLIP text model loads only when the index has image vectors, so a text-only vault never loads it | §5's one-transaction-per-file rule; text-only users pay nothing for CLIP |
| 17 Oct | **Backfill pass** at the end of every run (open item i): files indexed by older versions are brought up to date without OCR or text embedding. Interim kinds are reclassified and re-dated from the image header, mtime/doc_meta dates pick up a filename date, and missing thumbnails and CLIP vectors are made, 16 files per transaction | The fast path skips unchanged files, so without it an M2 index would need a full `reindex` (hours of OCR on a large folder) to get M3's kinds, dates, thumbnails and CLIP |
| 17 Oct | **Real CLIP on generated scenes** (24 images: a drawn sunset beach, a green field, a checkerboard, 21 text screenshots): each target was the top CLIP hit at z ≈ 4.5–4.8 ("sunset at the beach" 0.296 vs next 0.151), the only image the gate kept; "error 0x80070005" kept none (max z 0.52) and halved CLIP's weight. Embedding the 24 took 0.73 s; the first model download took ~60 s | Basis for `tests/test_clip_real.py`; the z = 2.0 threshold has a wide margin on clear visual queries |
| 17 Oct | **Ingest speed after M3** (same synthetic set, method and laptop as the M2 rows; now with classifier, dates, thumbnails and CLIP). Two runs, files/sec: **screenshot 0.23 / 0.24, photo 0.41 / 0.43, pdf 0.13 / 0.11**. Stage time: OCR 184 / 189 s, embed 16 / 18 s, **CLIP 8.0 / 7.3 s for 37 images (≈ 0.2 s each), thumbnails 2.0 / 1.9 s for 47 files**, everything else < 5 s. The CLI's first query loads bge (0.9 s) and CLIP-text (1.1 s) | Lower than this morning's 0.30 / 0.62 / 0.14, but not all of it is M3: the embed stage also rose from 9–11 s to 16–18 s although M3 embeds less (no path-chunk vectors), so the laptop was in its slower state (the drift noted in the embedding benchmark). The directly timed M3 stages (CLIP + thumbnails) add ≈ 10 s per 47 files. Re-measure interleaved, and on the VM (open item h) |
| 17 Oct | **`clip_min_cosine` = 0.26 (provisional)** for the gate's small-collection fallback (fewer than 20 candidates): a top-5 hit is kept only at cosine ≥ 0.26 (open item r). Calibrated with the real CLIP on 15 generated images (9 scenes, 6 text screenshots) and 17 queries: the 9 matching pairs scored 0.265–0.326 (lowest: green field); of 246 non-matching pairs the highest were 0.275 ("singular value decomposition" vs the dice picture), 0.258 and 0.252 (an error-code query vs text screenshots); "electricity invoice" stayed below 0.252. 0.26 keeps every match and passes 1 of the 246; the midpoint 0.27 would pass the same pair and lose the green field. Tune with the eval in M6 (open item o) | Without it a text-free photo came up for "electricity invoice" in a small collection, which the demo's *My uploads* scope will usually be. The ranges overlap, so no value is clean |
| 17 Oct | Owner's answers: (p) M4 builds the kind words only (*screenshot(s)*, *photo(s)/picture(s)*, *pdf*, *slides/deck/ppt*, *doc/notes*), sharing code with the kind chips; date words, the date-based eval queries and the "last week" smoke test are Later. (q) The 7 demo scenarios are listed in §10. The M4 Index tab keeps a manual "Index now" button in local mode; "Later" means automatic re-indexing; demo mode has no folder indexing, only uploads | Demo first: kind words make filtered queries work with little code; dates can wait |
| 17 Oct | **Calibration rule** (CLAUDE.md, §9): no threshold or weight is ever chosen from the owner's personal test files. Values are calibrated on generated data with a script in `bench/`, marked provisional, and tuned only in M6 on the `tune` half of the eval queries, reported on the `heldout` half. Every fix targets a general cause, with a test on generated files. This replaces "the owner checks its threshold on their real books" in the ranking-fixes row above: item n's threshold is calibrated on generated contents pages, and real books are a check only | The owner's real files are a few dozen queries on one person's library: fitting values to them would overfit and can't be reproduced by anyone else |
| 18 Oct | **Dense floor** (open item s): dense drops chunks below `dense_min_cosine` = **0.42 (provisional)**, at every collection size. Calibrated with `bench/calibrate.py dense` (real bge-small, 24 generated chunks in the index's styles: screenshot text, PDF pages, slides, notes, receipts, three ~800-character chunks). 35 matching pairs: 11 easy (0.698–0.832) and 24 hard paraphrases sharing almost no words, e.g. "contrastive learning for medical image segmentation" against a chunk on self-supervised pretraining on MRI scans (0.539–0.752; lowest "paper introducing the transformer" against an attention-paper chunk, 0.539). Unrelated pairs: 240 visual-query pairs 0.243–0.572 (p50 0.408), 805 non-matching text pairs 0.302–0.767. The ranges overlap, so the floor sits a margin of 2 std of the hard-pair scores (0.113) below the lowest match, rounded down: 0.12 of headroom, and it cuts 55% of the visual pairs and 25% of the non-matching text pairs (0.48 would cut 82% / 67% with 0.06 of headroom) | Owner: dense is what makes demo scenario 2 work, so a floor too high is worse than one too low. It doesn't close item s's own example: bge scores `bills.txt` 0.44 against "sunset at the beach", and "a chessboard" against a sourdough recipe 0.545, above the hardest real match. The visual-query rule (next row) handles those |
| 18 Oct | **Visual-query rule** (§7.4): when the all-terms list is empty, the query is neither quoted nor code-like, and CLIP has a strong hit, BM25 × 0.5 and each strong CLIP hit × 2; weaker gated hits keep the normal weight (owner's adjustment, so a matching document isn't pushed below a long list of photos). Strong: z ≥ `clip_strong_z` = **3.25**, or under 20 candidates cosine ≥ `clip_strong_cosine` = **0.28** (both provisional; the factors 0.5 and 2 too). Calibrated end to end with `bench/calibrate.py rule`: real CLIP and bge, 12 drawn scenes, 24 or 188 distractor images (text-free photos, desktop UI, phone chats, code editors), the 24 paraphrase chunks plus 12 texts that each share one word with a scene query, all under neutral filenames. Rule off: 1 of 12 scenes at #1, the rest #2–#4 behind a one-word match. 36 images: z 3.0 → 12/12, but a paraphrase falls #1 → #2; z 3.25 → 10/12, no paraphrase changes. 200 images: z 3.0 → 12/12, z 3.25 → 11/12, z 4.0 → 7/12; every level from 3.0 to 4.0 costs "kid's vaccination schedule" its #1 (#1 → #3 at 3.25), and "office internet password" goes #4 → #6. 16 images: cosine 0.28 → 12/12, and the rule fired on no paraphrase (0.27 fired on one) | Seen twice in the owner's real-file M3 test: when no file held every query word, a file matching one or two common words through BM25 and dense (2/61) outranked CLIP's strong gated match (1/61). Reproduced on generated files first. The paraphrase losses come from text queries whose top CLIP hit is a phone-chat screenshot that "looks like" the topic (z 4.3 and 5.2), so no z level below 4.3 avoids them; 3.25 is the lowest level that costs no paraphrase #1 among 36 images |
| 18 Oct | **Screenshot vs photo by CLIP** (§6.2 rule 4): rules 1–3 (screenshot filename, camera EXIF, PNG) stay. Any other image is a screenshot if its CLIP cosine to "a screenshot of a computer or phone screen" leads its cosine to "a photograph" by `clip_screenshot_margin` = **0.04 (provisional)**; the old size rules are the fallback while CLIP can't run. Only filename and PNG screenshots skip the angle classifier. Schema v3 adds `files.ocr_cls`, filled by kind for older rows. The end of each run relabels undecided images from stored vectors and re-OCRs only images whose classifier setting changed (owner: kind-only changes are relabelled, not re-OCR'd). Calibrated with `bench/calibrate.py kind`: 56 generated screen captures (desktop UI, phone chat, code editor, text page) and 42 photo-like pictures (noisy photos, photographed signs, drawn scenes) at forwarded sizes (720×1600, 1600×900, 1200×1600, 1080×2400…). Leads overlap (screens −0.003 to 0.103, photo-like −0.010 to 0.083); 0.04 got 47/56 and 32/42 right, the best balance (80%). Three-prompt lists did no better (78–81%), so the simplest pair stays | A WhatsApp photo has no camera EXIF and often a screen-like size (a 20:9 photo becomes 720×1600), so the size rules called it a screenshot and OCR skipped the classifier. Weak spot: generated photographed signs lean screenshot (lead up to 0.08), and so did a generated notebook-cover photo at 720×1600 (0.05; 0.03 at 1600×900); OCR is unaffected (the classifier stays on), only the kind is wrong. Generated "photos" aren't photographs, so item w checks the margin on real ones |
| 18 Oct | **Owner's real-file check after the M3 fixes:** 12 of 13 queries correct at #1. The miss: "handwritten notes on a black background" put a notebook-cover photo (OCR text "TRU NOTE") at #1 and the handwritten screenshot at #5. The owner reads it as CLIP's judgement, not fusion. Recorded under items o and w for M6; no fix now (calibration rule: reproduce on generated or corpus files first) | Real-world check of M3 before M4 |
| 18 Oct | **M4 choices (owner's answers to the M4 plan):** (1) the demo index's first slice is a ~50-file *generated* stand-in (drawn screenshots and scenes, generated PDFs, self-written decks and notes), marked `generated` in MANIFEST; the owner adds real public files and own non-personal photos before M6, and the demo index is rebuilt then. (2) A "Weaker matches" tail below the fused list (§7.4 step 4). (3) *notes* is not a kind word (§7.1). (4) Kind chips and kind words combine as a union. (5) The demo index stores repo-relative paths (§5). (6) Thumbnails and previews come from `/thumb/{file_id}` routes, not Gradio file URLs; `allowed_paths` stays empty (§8). (7) Uploads are incremental: later uploads index only new files, identical files are skipped by sha256 (§8). (8) Uploads are indexed one job at a time. (9) One custom UI theme that works on a phone. Highlight boxes and "Why this result?" stay in M5, "How it works" in M6 | (2) the floors and the gate exist to keep weak matches out of fusion, not off the page: in a small collection a visual query showed one card and nothing to browse. (3) As a pdf + doc filter, "notes" would drop both images from the owner's "handwritten notes" query. (4) An intersection of a chip and a contradicting word is an empty list. (5) The laptop's absolute paths don't exist on the VM and shouldn't be shown publicly. (6) Stricter than serving files by path. (8) RapidOCR stores per-call settings on the engine (see the 17 Oct `use_cls` row), and OCR uses every CPU |
| 18 Oct | **M4 build.** The app is Gradio mounted on a FastAPI app whose `/thumb/{id}` and `/preview/{id}` routes look files up in the DB (a session's need its `uuid4`). Result cards are HTML from pure functions (`vault/ui/cards.py`); a click goes back to Python as a file id through `gr.HTML`'s `trigger`, and "Show more" is client-side, since all 50 results are already in the page. `?q=…` opens the page on a search: a link to share, and how deploy checks and screenshots run. One theme of colour tokens for light and dark, with system fonts so the page loads nothing from the network; the phone layout was checked at 390 px in headless Edge (Gradio's container needs an explicit `width: 100%`, or a phone page widens to fit the search row). Uploads: only files inside Gradio's upload folder are read, and Gradio's copy is deleted once the session has its own. `app.py` sets `GRADIO_ANALYTICS_ENABLED=False` before Gradio loads | Gradio reads that variable for analytics and its version check whatever `Blocks(analytics_enabled=False)` says, so local mode would have called api.gradio.app (§11) |
| 18 Oct | **At most `max_sessions` = 20 upload sessions at once** (confirmed by the owner, 19 Oct): with all 20 open, Add files and the drop zone say "The demo is busy, try again in a few minutes", an upload is turned away whole, and searching keeps working for everyone; a visitor who has a session can still add to it (§8). Checked in a test with the default 20 and in a browser | Bounds the server's disk use at 20 × 200 MB |
| 18 Oct | **Demo index from the stand-in slice:** 52 generated files (18 screenshots, 5 of them tall phone scrolls; 10 drawn scenes and 5 photographed notices, one upside down, and a receipt; 10 PDFs: 4 papers, 2 scans, 3 slide-style, a ticket and a bill; 3 decks; 5 notes). `build-demo` on the laptop: 0 errors, 44 images or pages OCR'd (OCR 79 s), 0.8 MB with thumbnails. Smoke run with the real models: the right file at #1 for the error code, the traceback, the diffusion paper, the whiteboard, the phrase at the bottom of a tall scroll, the text inside a slide-style PDF's picture, the exact SVD sentence, Bayes in the scanned book, dice, the electricity bill, the upside-down notice and the Docker error. "sunset at the beach" puts `travel plan goa.docx` ("beach day… sunset is around 18:10") at #1 and the photo at #2 and in the Visual matches row. Wrong page: the agenda and overview pages (item n); noise: tiny chunks in dense (item x) | Generated files, so these are a smoke test, not an eval |
| 18 Oct | **VM benchmark (open item h)** on `Standard_D4as_v4` (AMD EPYC 7763, 4 vCPUs, 16 GB; resized in place 18 Oct), with the app running idle; method and scripts as in the laptop rows (`bench/`). **Embedding** (quantized bge-small, batch 32, `bench/bench_embed.py`): 11.5 / 11.6 / 11.8 chunks/s with ONNX Runtime's default threads, 11.7 with 4: steady, unlike the laptop's 9–18 swing. **OCR** (`compare_ocr.py`, 3 interleaved runs, 1920×1080): before (RapidOCR defaults) 3.06 / 3.08 / 3.07 s, recall 97.0% (detect 526 ms, recognise 2,599 ms); after (the `Config` defaults) 2.03 / 2.03 / 2.01 s, recall 99.9% (detect 135, recognise 1,900); after with ONNX Runtime's default threads 2.01 / 2.00 / 2.00 s. **Ingest** (the synthetic 47-file set, two runs): files/sec **screenshot 0.44 / 0.44, photo 1.01 / 0.99, pdf 0.17 / 0.17**; OCR 116.3 / 116.4 s, embed 9.1 / 9.0 s, CLIP 5.0 / 5.0 s, thumbnails 0.9 s; 62 images or pages OCR'd, 0 errors. **Two OCR engines × 2 threads:** 1.80 s per image vs 2.04 s for one 4-thread engine (+12% throughput). SQLite 3.45.1 | No VM-specific defaults: `ocr_threads` = min(8, CPUs) = 4 matches ONNX Runtime's default here, and embedding threads make no difference. Ingest is 1.4–2.4× the laptop's (whose M3 run was in its slow state). A second OCR engine adds 12% throughput, not enough to justify processes (CLAUDE.md). At these rates the synthetic set's 47 files take ~2.2 min, so a 100-file mixed upload is near the 5-minute target (§8); M5 times a real one |
| 18 Oct | **UI redesign (M5), owner-approved design in `design/`** with the owner's changes: the UI is called Cortex Vault (package and CLI stay `vault`); one screen, dark by default, light via `?__theme=light`, Geist served locally; search as you type (300 ms, ≥ 2 characters, changed text only, newest request wins) and Enter opens the selected result only if the results belong to the text in the box; file-type chips filter the page, kind words stay hard filters; groups Close / Visual (results only CLIP found) / Related (the weaker matches), Close and Visual ordered by their best item's fused rank, Related last; Found by shows each method's real rank for every result, with a Details section (RRF shares, CLIP gate) replacing the Show scores toggle; highlight boxes on OCR lines and the PDF text layer (words ≥ 3 characters, no stopwords, ≤ 10 per page; one lighter passage box for a meaning-only match); Esc closes the phone sheet first; `/file/{id}` downloads by id (404 for unknown ids, other sessions' files and anything path-like); Add files and the drop zone replace the Upload tab; in local mode a Folders panel, created only in local mode, replaces the Index tab | The owner approved the design pack. The chip change is the design's: counts and groups describe the results on screen. Grouping by "only CLIP found it" puts each result in one group, so the arrow keys never visit a file twice |
| 18 Oct | **UI redesign built and deployed** (8c69587, cd0fcfb). Search latency from the stage timers, `bench/bench_query.py` (16 queries × 5 rounds on the demo index, vectors cached, after a warm-up): **VM (D4as_v4) p50 28.5 ms, p95 37 ms** (dense 9 ms, CLIP 16 ms, hydrate 3.5 ms); laptop p50 64 ms, p95 83 ms. So the 300 ms typing delay stays. Two choices made while building: (1) the 20 shown before "Show N more" are the top 20 by fused rank, each in its group, not the first 20 in display order, which hid a visual match at #2 behind a long Close group; (2) when nothing is a close or visual match, "No close matches for …" shows above the related matches, since the weaker-matches tail (§7.4) means a query almost never has zero results | Well under the ~0.5 s at which the delay would go up (owner's rule) |
| 18 Oct | **Two-phase indexing (item g):** phase 1 lists each new file (kind, date, native text, thumbnail, path chunk; one transaction; `files.stage = 'listed'`), phase 2 reads it (CLIP and the kind decision, OCR, all text vectors; one transaction; `stage = 'ready'`), newest first in both. A run stopped in phase 2 is finished by the next. The demo index was rebuilt for schema v4 and gives the same top results | Names and native text are searchable within seconds while OCR takes seconds per image |
| 18 Oct | **Early stop for unreadable PDFs (item k): `pdf_ocr_stop_conf` = 0.72 (provisional)**, the mean recognition confidence of the lines found on a PDF's first 3 OCR'd pages *with ink* (blank pages don't count). Below it, the rest of the PDF gets no OCR and the report names it. RapidOCR now returns every recognised line (`text_score` 0) and our 0.5 floor applies as before, so indexed text is unchanged. Calibrated with `bench/calibrate.py ocr-stop` on 20 generated pages: 3-page probes of printed pages (dense, small type, a sparse title, a dot-leader contents page, noisy scans, a figure, two columns) 0.887-0.994; of handwriting-like scribbles 0.553-0.631, including one behind a printed cover; legible warped text 0.993 (continues). The first rule tried, kept/found lines and words per page, could not separate them: the recogniser reads scribbles as confident garbage (76-96% of lines above 0.5, 10-41 "words" a page), as many as a sparse printed page | Errs low: a value too high would drop a real scanned book's text, one too low only costs OCR time. Generated scribbles aren't handwriting, so the owner's handwritten PDF is a check in M6 (item o) |
| 18 Oct | **Upload caps final: 100 files, 25 MB each, 200 MB per session (unchanged).** `bench/bench_upload.py` on the D4as_v4 VM (app running idle): a generated mixed upload of 100 files, 115 MB (30 desktop and 10 phone screenshots, 25 camera photos 4000×3000, 10 PDFs with a text layer, 3 scanned, 2 slide-style, 8 Word, 6 PowerPoint, 6 text) was **listed in 8.0 s** (names and native text searchable) and **fully read in 207.7 s** (OCR 164.7 s for 82 images and pages, embed 21.0 s, CLIP 7.9 s, extract 15.6 s). Files/sec: screenshot 0.50, photo 0.98, pdf 0.17, doc 5.2, slides 10.9 | Inside the ≤ 5 min target (§8). Not covered: 100 tall scrolling screenshots would take longer (several OCR strips each); the caps don't bound that, but two-phase indexing lists them in seconds |
| 19 Oct | **OCR model comparison (item j), a measurement; kept small: models frozen.** PP-OCRv6 *tiny* is PaddlePaddle's official ONNX export on the HF Hub (`PaddlePaddle/PP-OCRv6_tiny_rec_onnx` @2612ab37, `…_tiny_det_onnx` @2ba1506c, Apache-2.0), fetched by `bench/fetch_ocr_models.py` into the gitignored `bench/generated/`. `bench/compare_ocr.py`, laptop, 3 interleaved runs on 8 generated 1080p screenshots: **small (bundled) 2,566 ms, recall 99.9%; small detector + tiny recogniser 1,233 ms, 99.7%; tiny detector + tiny recogniser 719 ms, 99.0%**. All six real-OCR tests (the four M2 done-when tests among them) pass with both tiny variants. Indra (Datalab, `datalab-to/indra-ocr`, weights under a modified OpenRAIL-M, its own package) was not installed (owner) | The owner froze the models (next row). The engine's model-path and character-list options stay (`ocr_rec_keys`); the defaults are unchanged |
| 19 Oct | **Models frozen** at bge-small (`BAAI/bge-small-en-v1.5`, quantized), CLIP ViT-B/32 (`Qdrant/clip-ViT-B-32-vision` + `-text`) and PP-OCRv6 small (bundled in the rapidocr wheel). A model changes only if the M6 eval shows clearly bad results traced to that model, and only with the owner's OK | Owner's decision: stable models for the eval and the demo; speed comes from indexing in two phases, not from swapping models |

### Open items

Status is `open`, `done` (with the commit), or `moved to Mx` (with the reason). See CLAUDE.md for when to review them.

| ID | Item | Milestone | Status |
|---|---|---|---|
| a | Common words ("the", "is", "where") in the BM25 OR-query match nearly every chunk and add noise to RRF ranks. Decide whether to drop them when other terms remain, using the eval. Seen on the owner's real files: pattern (d) in item l | M3 | done: 0292950 |
| b | The UI keeps one text and one image `VectorIndex` and rebuilds them after indexing; `search()` already accepts a prebuilt `text_index` and `image_index` | M4 | done: 137d5f2 (`vault/library.py`: cached per index, rebuilt after each indexing run; a session copy has its own) |
| c | Date tests must set the Office `created` property explicitly: python-docx/python-pptx templates carry a 2013 creation date | M3 | done: 4ae5132 |
| d | 3 of the owner's real test PDFs have no text layer (scanned/handwritten): use them as real-world cases for scanned-page OCR. Scanned-page OCR shipped in M2; awaiting the owner's run (`vault reindex`, since unchanged files skip extraction). Handwriting is out of scope (§1, §17). Expected on the owner's set: `edited_ticket.pdf` has a text layer and is not OCR'd; the 414-page image-only book OCRs 30 pages and hits the cap; the 41-page handwritten notes OCR 30 pages with little usable text (see k); the 212-page scanned book with an OCR text layer OCRs only its ~9 blank or cover pages, not 30 (image regions under native text are skipped). The owner confirms on their run | M2 | done: confirmed on the owner's run, 17 Oct (scanned-page OCR in f611e74) |
| e | The path chunk includes the file extension, so a query containing "pdf" (as in probability density) matched every PDF filename. Consider dropping the extension from the path-chunk text. See pattern (b) in item l | M3 | done: 60ea1dd |
| f | Dense top-100 chunks can be crowded out by one long document (in the real-world test only one book appeared in the dense list). Consider retrieving more chunks before collapsing to files; measure in the eval. See pattern (a) in item l | M6 | open |
| g | Two-phase indexing. **Phase 1** (per file, fast): list the file with its filename/path chunk (FTS) and thumbnail. **Phase 2** (background): OCR plus all embeddings. Images aren't keyword-searchable until OCR runs and OCR costs seconds per image, so OCR belongs in phase 2 with the embeddings. Dense search skips chunks without vectors; the UI shows "listed / searchable / meaning-ready" progress. Matters most for demo uploads. Moved up to M5 (§14 reorder, 17 Oct): it's what makes demo uploads feel fast | M5 | done: 67c84d8 (`files.stage` listed / ready, schema v4; §6.1). The UI shows the upload outcome after phase 1 and "Reading X of Y uploads" in phase 2 rather than three separate listed / searchable / meaning-ready counts |
| h | At M4, resize the current VM in place from B2ls_v2 to `Standard_D4as_v4` (4 vCPU, 16 GB, non-burstable) by Stop → Size → Start: same disk, IP, NSG and host key, so no new VM. Quota in India South Central: Total Regional = 6, DASv4 = 4, FALSv6 = 0, DASv5 = 0, with no increases possible on Azure for Students. Then benchmark there with `bench/` (embedding method above; `python bench/compare_ocr.py`; the ingest-speed run; two files OCR'd in parallel) and set its OCR/embedding defaults separately if needed. Check `sqlite3.sqlite_version` ≥ 3.44 there (FTS5 secure-delete). The VM is needed only until 30 Oct 2026; delete the resource group after. Steps: `deploy/azure.md` §9 | M4 | done: resized by the owner 18 Oct; benchmark in §19 (18 Oct), no VM-specific defaults needed; SQLite 3.45.1 |
| i | Images indexed in M2 carry an interim kind (`kind_reason='interim:ext'`) and the mtime date. M3 must reclassify and re-date them in place (EXIF, filename, dimensions only), without re-running OCR or re-embedding | M3 | done: 4ae5132 (classifier, dates) + 6073088 (backfill: no OCR, no text re-embedding) |
| j | Time-boxed experiment (~30 min): a lighter English-only recognition model (e.g. PP-OCRv4 English mobile) from the HF Hub only (CLAUDE.md; it isn't in the rapidocr wheel). Adopt it only if `bench/bench_ocr.py` shows it ≥ 30% faster with word recall ≥ 99%, and the four M2 done-when tests still pass. Otherwise record the numbers and keep the bundled model. Context: 2.4 s per 1080p screenshot after M2 tuning vs a ≤ 1 s target, mostly recognition | M5 | done as a measurement (§19, 19 Oct): tiny recogniser 52% faster at 99.7% recall, tiny both 72% at 99.0%; kept small, models frozen |
| k | Early stop for PDF OCR: if the first 3 OCR'd pages of a PDF give almost no confident text (e.g. handwriting), skip its remaining pages and say so in the index report. Today a 41-page handwritten PDF costs 30 pages of OCR for little usable text | M5 | done: 2c6be9a (mean recognition confidence of the first 3 inked pages below `pdf_ocr_stop_conf` 0.72, provisional; §19). The owner's 41-page handwritten PDF is a check, not a value to fit |
| l | Ranking patterns from the owner's real-file test (43 queries): **(a)** a short screenshot whose OCR text has a rare exact query word ranks below a long lecture-transcript `.docx` that matches only loosely through dense retrieval (3 times; the transcript is #2 for ~15 of the 43 queries); **(b)** path-only chunks ("folder / filename") show up as weak dense matches for unrelated queries; **(c)** a book's table-of-contents page (dot leaders) ranked #1 while the real section pages weren't in the top 3; **(d)** common-word queries put the right screenshot only at #3. Agreed 17 Oct (§19): (d), (a), (b) with (e) in M3, in that order, each with a fixture test; (c) moved to M6 as item n | M3 | done: (d) 0292950, (a) f93f830, (b)+(e) 60ea1dd; (c) is item n |
| m | The stray-CJK cleanup works within a line, so an icon detected as its own one-character line (e.g. `品` alone) is kept. If such lines still show up in snippets, drop a lone one-character CJK line when the rest of the image is Latin | M6 | done: b180a6f |
| n | Table-of-contents pages (pattern c in item l). Flag pages with ≥ 5 "dot leader + page number" entries as `pdf_toc` (before stripping), strip leader runs from chunk text, leave TOC chunks out of dense, and let a TOC chunk stand for its file only when none of the file's other chunks is in that list. Stored text is enough to re-flag existing PDFs without re-extracting. Test: a fixture PDF with a contents page and a section page shows the section page. The threshold is calibrated on generated contents pages (§9 tuning rule); the owner's real books are a check only, never a reason to change the value. First in M6, before item o. **Same cause without dot leaders** (demo stand-in corpus, 18 Oct): "slide about gradient descent" shows slide 1 of both ML decks, the agenda that lists "gradient descent", not slide 3 or 4; "where is singular value decomposition defined" shows page 1 of `matrix-decompositions.pdf`, the overview naming the SVD, not page 3. The page shown is BM25's best chunk, and BM25 favours a short page that names the topic over the page that covers it. `scan_0001.pdf` has a dot-leader contents page competing with its Bayes section | M6 | open |
| o | Tune the RRF weights and k and the provisional values, with the eval's `tune` half (§9), after item n: `clip_min_cosine` (0.26, item r), `dense_min_cosine` (0.42), `clip_strong_z` (3.25), `clip_strong_cosine` (0.28), the visual-query factors (0.5, 2) and `clip_screenshot_margin` (0.04, item w). Weight changes alone can't fix pattern (a) of item l (see §19), so this is tuning on top of the M3 fixes, not instead of them. If the eval shows the visual-query rule firing on text queries through look-alike screenshots (as phone chats did on generated data, §19, 18 Oct), consider also requiring a gap to the next CLIP hit; consider an ablation row without the rule. **Real-file case (owner, 18 Oct):** "handwritten notes on a black background" puts a notebook-cover photo (OCR "TRU NOTE") at #1 and the handwritten screenshot at #5, which the owner reads as CLIP's judgement, not fusion. Reproduce the pattern (a photographed cover vs a screenshot of handwriting on a dark background) on generated or corpus files before changing anything | M6 | open |
| p | The query parser is now Later (§14), but §9's `filtered` category (≥ 6 queries with kind or date words), the fixed-`now` relative-date queries, and the §12 smoke test "screenshots from last week" all need it. Decide before M6: build the kind words (and maybe dates) anyway, or drop or skip those queries until the parser is picked | M4 | done: ee6cd8b (kind words only, without *notes*, §7.1; chips and words are a union); date-based eval queries and the "last week" smoke test are Later (§9, §12) |
| q | M6 says the corpus covers "the 7 demo scenarios"; the spec doesn't list them yet. The owner names them (§10 and §12 are the closest lists) | M6 | done: listed in §10 |
| r | The gate's small-library rule (§7.3: fewer than 20 candidate images → keep the top 5) means that in a small collection every text query lifts up to 5 images through CLIP, relevant or not: with the fake CLIP, a text-free photo came up for "electricity invoice". The demo's *My uploads* scope will usually have fewer than 20 images. Owner to decide: keep it, or e.g. keep the top 5 only above the candidates' mean, or at reduced weight | M4 | done: e5c45ae (`clip_min_cosine`, provisional 0.26) |
| s | Dense always returns its top k whatever the similarity, so in a small collection the only text file is dense #1 for *any* query: with the real CLIP, "sunset at the beach" put the sunset photo (CLIP #1) in a tie with an unrelated `bills.txt` (dense #1). The same small-collection effect as item r, on the text side; the demo's *My uploads* scope will show it. Consider a minimum cosine for dense hits too, measured with the eval | M6 | done: eef56c8 (`dense_min_cosine` 0.42, provisional); the example tie itself is closed by the visual-query rule, 8b208e2 (bge scores `bills.txt` 0.44, above the floor) |
| t | A "Visual matches" thumbnail row in the Search tab: CLIP's gated hits shown as their own row above or beside the ranked list, so a strong visual match is visible even when text files outrank it in the fused list (owner's real-file M3 test) | M4 | done: 30ef0d9 (`SearchResponse.visual`), 249e4b4 (the row) |
| u | Scale test: ~1,000 generated files (the `bench/make_bench_corpus.py` kinds, scaled up) with planted targets for a fixed query set. Measure Recall@10 and search latency p50/p95, with the stage timers. Checks that the provisional thresholds and the CLIP gate hold up at a size between the demo corpus and a real library | M6 | open |
| v | Visual queries lose to weak word matches: when no file holds every query word, a file matching one or two common words through BM25 and dense outranked CLIP's strong gated match (owner's real-file M3 test: a movie frame at #4 for "three women standing outside", handwritten notes at #5 for "handwritten notes on a black background") | M3 | done: 8b208e2 (visual-query rule, §7.4; reproduced and calibrated on generated files) |
| w | Forwarded photos labelled screenshots: no camera EXIF plus a screen-like size, so the kind was wrong and OCR skipped the angle classifier (owner's real-file M3 test, WhatsApp). Fixed by CLIP deciding rule 4 (§6.2). Open part: check `clip_screenshot_margin` (0.04) on real photos and screenshots, using the corpus's MANIFEST kinds (public files, split like the eval queries), especially photographed notices and whiteboards (scenario 4), which lean screenshot on generated data. Also see item o's real-file case: CLIP ranked a notebook-cover photo above a handwritten screenshot | M6 | open (fix done: 1e8747a, 8a7daaa) |
| x | Tiny chunks in dense: one- or two-character OCR chunks from text-free photos ('日' on the checkerboard, 'M' on the mountains, '0 田' on the dice) and one-word slides ('Summary') clear `dense_min_cosine` for unrelated queries: dense #2–#4 for "sunset at the beach" on the demo index (18 Oct). Consider a minimum content length below which a chunk gets no vector (it stays in BM25); calibrate it on generated data in `bench/` | M6 | open |
| y | Snippets of dense-only matches show the chunk's first ~200 characters, which for a phone screenshot is its status bar ("08:12 87% Browser"). Show the window most similar to the query instead, or skip leading short OCR lines | M5 | open |

---

## 20. Changelog

**v2.5 (19 Oct 2026, M5)**
- The UI is Cortex Vault, from the approved design in `design/` (§8): one screen, grouped results, a preview with highlight boxes, Found by and Details, search as you type, keyboard, dark by default, Geist served locally.
- Two-phase indexing (§6.1, schema v4 `files.stage`); early stop of OCR for unreadable PDFs (§6.3, `pdf_ocr_stop_conf`).
- Upload caps final; demo busy at 20 sessions (§8). Models frozen (§19).

**v2.4 (18 Oct 2026, M4)**
- Kind words in the query (the M4 subset, without *notes*), united with the kind chips (§7.1).
- Weaker matches below the fused list, so the floors and the gate never cut the list short (§7.4 step 4); the Visual matches row (item t).
- The UI: Search tab with cards, "Show more" to 50 and a detail panel; Upload tab with incremental per-session uploads and the scope toggle; local Index tab; one custom theme (§8). Files are served by id only, through the app's own routes.
- The demo index stores repo-relative paths (§5) and is built from a generated ~50-file stand-in slice (§10, §19).
- Deployed on the VM, resized to `Standard_D4as_v4`; the M0 probe is removed (§13, §15).

**v2.3 (18 Oct 2026)**
- Calibration rule: thresholds and weights come from generated data and are tuned only in M6 on the eval's `tune` half (§9, CLAUDE.md).
- Dense floor `dense_min_cosine` (§7.2); visual-query rule with strong CLIP hits (§7.3, §7.4); screenshot vs photo by CLIP for images no name, EXIF or PNG rule decides, with re-OCR only on a classifier change (§5, §6.2).

**v2.2 (17 Oct 2026)**
- Milestones M4–M8 reordered demo first, with a "Later" list of optional items (§14, §19). §8 updated to match: ranked list with "Show more", *Sample files* scope, upload caps set in M5.

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
