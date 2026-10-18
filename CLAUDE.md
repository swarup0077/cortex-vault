# CLAUDE.md — Personal Search Vault

The source of truth is **SPEC.md**. Read it before starting any task. If a request conflicts with the spec, say so and ask; do not quietly diverge.

## How we work
- Do **one milestone per session** (SPEC §14). Start by restating the milestone's "done when" criteria. End by running the tests, then summarising what's done, what's not, and any spec changes.
- **Open items** (SPEC §19): at the start of every milestone, list the open items tagged with that milestone and include them in the plan. Before declaring the milestone done, update each one: mark it `done` (with the commit), move it to another milestone with a reason, or add new rows for anything found along the way.
- Work in small steps: write or adjust a test, implement, run `pytest -q`, commit. Use one commit per coherent change with an imperative message (`ingest: add rename detection by sha256`).
- When a design choice comes up that isn't in the spec, propose it in one or two lines and ask. Record agreed choices in SPEC §19 (Decisions log).
- Don't start stretch goals unless asked.

## Thresholds and weights
- No threshold or weight is ever chosen from the owner's personal test files. Calibrate it on generated data, mark the value **provisional** (in `config.py` and in SPEC §19), and tune it only in M6, on the tuning half of the eval queries, reporting on the other, held-out half (SPEC §9).
- Every fix targets a general cause and comes with a test on generated files. A real-file failure is a symptom to reproduce on generated data, not a value to fit.
- Calibration scripts live in `bench/`, so every provisional value can be re-measured.

## Environment
- Windows laptop, CPU only, Python **3.12**, venv at `.venv`.
  - Activate: `.venv\Scripts\activate`
  - Install: `pip install -r requirements.txt`
- The code must also run on Linux (the hosted demo) and macOS. Keep OS-specific calls, such as opening a file, in `vault/platform.py`.
- Always use `pathlib`. Paths may contain spaces and drive letters. Never build paths with string concatenation.
- Multiprocessing on Windows needs the `if __name__ == "__main__":` guard. Prefer batching over processes unless profiling shows OCR is the bottleneck.

## Commands
```
python -m vault index <folder> [<folder> ...]   # incremental index
python -m vault reindex                          # full rebuild (model change)
python -m vault search "query" [--no-rerank] [--debug]
python -m vault stats
python -m vault eval                             # writes eval/results.md + .json
python -m vault build-demo                       # builds demo_index/ from corpus/ (repo-relative paths, refuses GPS)
python -m vault scrub-exif <folder>              # removes GPS EXIF from the images in a folder
python corpus/make_standin.py                    # regenerates the generated stand-in slice of corpus/
python app.py                                    # Gradio UI (VAULT_MODE=local|demo)
pytest -q                                        # everything
pytest -q -m "not slow"                          # skip tests that load real models
```
Tests that load real models are marked `@pytest.mark.slow`. Run the quick set while iterating, and the full set before each commit.

## Code rules
- `vault/` is core and must not import Gradio. Only `app.py` and `vault/ui/` touch the UI.
- Models are lazy singletons in `vault/models.py`. Never load a model at import time in other modules.
- Keep the search pipeline as pure functions (parse → retrieve → fuse → rerank → snippet) so each stage is testable alone.
- Use type hints on public functions, dataclasses for records (`ParsedQuery`, `SearchResult`, `IndexReport`), and `logging` rather than `print` (the CLI may use `rich` for output).
- A file's DB writes happen in one transaction: files, chunks, FTS rows and vectors. FTS5 rows are deleted explicitly.
- Tests never touch personal files. Generate fixtures in `tests/conftest.py`:
  - images with text drawn by Pillow;
  - PDFs with known text on known pages via PyMuPDF;
  - images with EXIF written by Pillow;
  - `.docx` / `.pptx` files built with python-docx / python-pptx;
  - a tall 1080×6000 image and a two-column image for the OCR tests;
  - a fixed `now` for the parser.

## Don't
- Don't call any cloud API or send file contents anywhere. The only network access allowed is model downloads from the HF Hub.
- Don't add a dependency without adding it to `requirements.txt` and giving one line on why in the commit message. No PyTorch; everything is ONNX via fastembed and RapidOCR.
- Don't commit `data/`, model caches, `.venv/`, or anything outside `corpus/` that could be personal.
- Don't let demo mode read a user-supplied path. Serve only by DB id or session id.
- Don't invent performance numbers in the README. Measure them with the CLI's stage timers and `vault eval`.
