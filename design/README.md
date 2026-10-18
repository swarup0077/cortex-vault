# Cortex Vault UI: design pack

The approved design for the M5 UI redesign. Everything here uses fictional sample data, so it is safe to commit.

| File | What it is |
|---|---|
| `reference.html` | Open in a browser. Toolbar switches Home / Results / Phone / States and Dark / Light. Arrow keys, Enter and the file-type filter work, so the behaviour is visible too. |
| `cortex.css` | All tokens and components. Port this into the Gradio stylesheet; keep the class names. |
| `fonts/` | Geist 400/500/600 and Geist Mono 400/500 (woff2). SIL Open Font License 1.1: `OFL.txt` must ship with the fonts. |
| `screenshots/` | Renders of reference.html, for a quick look without a browser. |
| `logo/` | `cortex-vault-icon.svg` (app icon), `cortex-vault-icon-16.svg` (simplified, no ticks, for 16 px), `cortex-vault-mark-on-dark.svg` / `-on-light.svg` (no tile), `favicon.ico` (16 simplified + 32 + 48), PNGs at 180/192/512. |

The page makes no network requests: fonts and icons are local. Keep it that way in the app.

## Screens

**Header** (52 px): logo + "Cortex Vault" · status · scope control (demo only: Samples / My uploads / Both) · **Add files**, the only primary button on the screen.

**Search bar:** the input, then the result count and search time on the right ("14 results · 0.21 s"; both come from the app, never typed in). Below it, the file-type filter with counts: All, Screenshots, Photos, PDFs, Slides, Docs.

**Results (left) and preview (right, 440 px, sticky).** Results are grouped like Windows Recall. This changes presentation only, not ranking:

| Group | What goes in it |
|---|---|
| Close matches | results above the current "Weaker matches" divider |
| Visual matches · found by what is in the picture | the existing CLIP visual row, as thumbnails (the 320 px WEBP the indexer already makes) |
| Related matches | results below the divider |

Inside each group the order is the fused rank. Keep the current rule of top 20 plus "Show N more" up to 50, where N is the real remaining count.

**Result row:** tinted file-type tile · file name · meta (type · date, or page / slide number) · one-line snippet with matched words in `<mark>` · "Enter" hint on the selected row only.

**Preview pane:**
- image: thumbnail or rendered page;
- OCR highlight box over the matched text, if boxes are stored;
- title and meta line;
- facts:
  - **Found by:** which retrievers returned it and its rank in each, e.g. "Exact words (rank 1) and meaning (rank 2)". If that isn't available, leave the row out rather than guess.
  - **Text read by:** OCR / the PDF's own text layer / document text / no text found. Say "on this server" in demo mode and "on this computer" in local mode.
  - **Size.**
- actions: Download (demo) or Open (local) · Copy text · Copy link to this result.

**Home:** centred column, 760 px max. Label "Search N files by words, meaning, or what is in a picture", a 56 px search box with a Ctrl K hint, two columns ("Try a search" with the search type on the right; "Recently added"), a dashed drop zone, and the honest note about the three models. In local mode, replace the demo note and the drop zone with the indexed folders.

**Footer:** mode line ("Demo server · your uploads are deleted after 30 minutes", or the local equivalent) and key hints.

**States** (see the States view in `reference.html`):
- No results: a one-line suggestion.
- Still reading uploads: header status shows "Reading X of Y uploads", plus a thin banner with a progress bar; search keeps working.
- After an upload: each file is listed as added, already in your uploads (sha256 duplicate), or not a supported type.

## Behaviour

- **Combobox pattern:**
  - Focus stays in the search box.
  - ↑/↓ moves the selection (`aria-activedescendant`) and updates the preview.
  - Enter opens the selected result: download by DB id in demo mode, open through `vault/platform.py` in local mode.
  - Esc clears the box.
  - Ctrl K (or `/`) focuses search from anywhere.
- **No Ctrl C / Ctrl D / Ctrl L shortcuts.** They belong to the browser (copy, bookmark, address bar).
- **Preview updates:** either from data rendered with each result, which avoids a round trip, or from the server. Either is fine.
- **Filter chips:** they hide other types and hide empty groups. Counts come from the full result set.
- **Phone (under 720 px):**
  - The scope control is hidden (Both) and the status moves to its own line.
  - Add files becomes a 44 px "+" button.
  - The filter chips scroll sideways and thumbnails show two per row.
  - Tapping a result opens the preview as a full-screen sheet with a close button.
  - The search input is 16 px so iOS doesn't zoom into it.

## Visual rules

- **Theme:** dark by default; light through Gradio's `?__theme=light`. Light tokens live on `:root`, dark tokens on `.dark` (the class Gradio puts on `<body>`).
- **Brass (`--cv-accent`)** is used only for the focus ring, highlight boxes and the logo. It never fills a button. In the light theme `#9A6B12` is about 4.4:1 on the page, so don't use it for body text.
- **Buttons:** one primary per screen (ink fill). Everything else is a ghost or outline button.
- **No decoration:** no drop shadows, gradients or glows. Borders are 1 px hairlines. Radii are 6 / 8 / 10 / 12.
- **Colour use:** type tints appear on file-type tiles only.
- **Type:** Geist 400 / 500 / 600; never bold 700. Geist Mono for counts, times, sizes and key hints.
  - Sizes: 12 for section titles and meta, 13 for snippets and chips, 14 base, 15 for the brand, 16 for the preview title, 17–18 for the search input.
- **Honest copy:**
  - No claims about features the app doesn't have: no encryption, no match percentages, no tags, no sync.
  - Every number on screen comes from the app.

## Gradio notes

- **Fonts:** Gradio's built-in themes load fonts from Google Fonts. In local mode that is a network request.
  - Use a theme with plain font names, e.g. `gr.themes.Base(font=["Geist", "system-ui", "sans-serif"], font_mono=["Geist Mono", "monospace"])` (strings, not `gr.themes.GoogleFont`), and serve `fonts/` from a static path.
  - Check the browser's Network tab: there should be no requests to `fonts.googleapis.com` or `fonts.gstatic.com`.
- **Result HTML:** build it on the server for `gr.HTML`. Pass every file name, path, snippet and OCR string through `html.escape` before inserting it; the only tags our code adds are `<mark>` and the layout. Uploaded file names and OCR text are untrusted on the public demo.
- **Code location:** custom JS goes in `vault/ui/static/cortex.js`, loaded through `gr.Blocks(head=...)`. HTML builders live in `vault/ui/`; `vault/` core stays free of Gradio.
- **Favicon:** `launch(favicon_path=".../logo/favicon.ico")`.
- **Gradio's own footer:** hide it with CSS.

## Suggested repo layout

```
design/                     this pack (reference only, not served)
vault/ui/static/cortex.css  ported from design/cortex.css (font URLs adjusted)
vault/ui/static/cortex.js
vault/ui/static/fonts/      the five woff2 files + OFL.txt
vault/ui/static/logo/       favicon.ico, icon SVGs
```
