"""The preview pane always shows the selected row (owner's bug report, 20 Oct), in a real browser: the app runs
in-process on the demo corpus (demo mode on the demo index, read-only; local mode on a copy of it, never the
owner's own index), and `tests/browser/preview_check.mjs` drives headless Edge or Chrome through the DevTools
protocol. Each run types one query, then another one fast with a half-typed search in between, while every
`/detail` response is held back and then let through newest first, so the older ones land last.

The bug: Gradio updates an HTML part by syncing it node by node in place, and a <template>'s content isn't a child
node, so the preview copies kept an earlier search's previews under the new file ids: row 03, a photo, showed
"Result 03 / PDF" and the PDF that was row 03 before. Skipped without Node 22+ or a Chromium-family browser.
"""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from vault.config import REPO_ROOT, Config

pytestmark = [pytest.mark.slow,  # pandas warnings from Gradio's own queue, run in-process here
              pytest.mark.filterwarnings("ignore:'future.no_silent_downcasting' is deprecated"),
              pytest.mark.filterwarnings("ignore:The copy keyword is deprecated")]

DRIVER = Path(__file__).resolve().parent / "browser" / "preview_check.mjs"
# On the demo corpus: "gradient descent" has a PDF at row 3, "electricity bill" a photo
QUERIES = ["gradient descent", "electricity bill"]
SCENARIOS = [  # how row 3 is chosen; with the page's aborts off, so every older /detail response really arrives
    {"nav": "keys", "noAbort": True},
    {"nav": "updown", "noAbort": True},
    {"nav": "click", "noAbort": True},
    {"nav": "click", "phone": True, "noAbort": True},  # a tap opens the phone sheet
    {"nav": "keys", "phone": True, "noAbort": True},
    {"nav": "keys"},  # as the browser really runs it: superseded requests are aborted
]


def _browser() -> str | None:
    if os.environ.get("CV_BROWSER"):
        return os.environ["CV_BROWSER"]
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if Path(p).is_file():
            return p
    for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser", "chrome"):
        if found := shutil.which(name):
            return found
    return None


def _node() -> str | None:
    node = shutil.which("node")
    if not node:
        return None
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()  # v24.15.0
    return node if out.startswith("v") and int(out[1:].split(".")[0]) >= 22 else None


BROWSER, NODE = _browser(), _node()
needs_browser = pytest.mark.skipif(not (BROWSER and NODE), reason="needs Node 22+ and Edge, Chrome or Chromium")


def _context(mode: str, tmp: Path):
    from vault.library import Library
    from vault.session import SessionManager
    from vault.ui import views
    from vault.ui.layout import UiContext

    demo = REPO_ROOT / "demo_index"
    if mode == "demo":
        cfg = Config(data_dir=demo)
        lib = Library(cfg, read_only=True, base_dir=REPO_ROOT)
        return UiContext("demo", lib, suggestions=views.DEMO_SUGGESTIONS, upload_dir=tmp / "uploads",
                         sessions=SessionManager(lib, cfg, tmp / "sessions"))
    copy = tmp / "index"  # local mode on a copy of the demo index: never the owner's data/vault.db
    shutil.copytree(demo, copy)
    return UiContext("local", Library(Config(data_dir=copy), base_dir=REPO_ROOT), suggestions=views.LOCAL_SUGGESTIONS)


@pytest.fixture
def served(request, tmp_path, real_clip):
    """The app for `request.param` ("demo" or "local"), served in a thread on a free port."""
    import uvicorn

    import app
    from vault.ui.layout import build_blocks
    from vault.ui.server import make_app

    ctx = _context(request.param, tmp_path)
    ctx.library.search("warm up")  # load the models before the browser types
    application = make_app(build_blocks(ctx), ctx.resolve, demo=ctx.demo,
                           max_file_size=ctx.sessions.cfg.upload_max_bytes if ctx.demo else None)
    with socket.socket() as s:
        s.bind((app.HOST, 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(app.server_config(application, port))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    yield f"http://{app.HOST}:{port}/"
    server.should_exit = True
    thread.join(15)


def check(url: str, **scenario) -> dict:
    options = {"url": url, "browser": BROWSER, "queries": QUERIES, "row": 3, **scenario}
    out = subprocess.run([NODE, str(DRIVER), json.dumps(options)], capture_output=True, text=True, encoding="utf-8",
                         timeout=180)
    state = json.loads(out.stdout)
    assert "error" not in state, state.get("error")
    return state


@needs_browser
@pytest.mark.parametrize("served", ["local", "demo"], indirect=True)
def test_the_preview_is_always_the_selected_rows(served) -> None:
    for scenario in SCENARIOS:
        s = check(served, **scenario)
        row, pane, what = s["selected"], s["pane"], json.dumps(scenario)
        assert s["query"] == QUERIES[-1] and row["visibleIndex"] == 3 and row["number"] == "03", what
        assert row["type"] == "photo", what  # the case reported: row 03 a picture, row 03 before it a PDF
        # header number, type, file name and picture all from row 3's file id, in this render
        assert pane["kicker"] == "Result 03 / Photo" and pane["title"] == row["title"], what
        assert pane["img"] is not None and f"/preview/{row['id']}" in pane["img"], what
        assert pane["file"] == row["id"] and pane["render"] == s["render"], what
        # the slow /detail responses: only the latest one, for this row in this render, landed
        assert pane["detailFor"] == f"{row['id']}:{s['render']}", what
        assert s["held"] >= 2, what  # the half-typed search and the earlier rows asked for previews too
        if scenario.get("noAbort"):
            assert s["released"] == s["held"], what  # every older response did arrive, and was dropped
        tapped = scenario.get("phone") and scenario["nav"] == "click"
        assert pane["open"] is bool(tapped), what  # on a phone the sheet opens on a tap, not on a key


@needs_browser
@pytest.mark.parametrize("served", ["demo"], indirect=True)
def test_the_upload_report_shows_the_latest_upload(served, tmp_path) -> None:
    """The bar's report rows are a hidden copy keyed by their upload now, not a <template> (the same cause as the
    preview's). A regression check: in this flow Gradio removed the old template between uploads, so the report
    was right before the change too."""
    uploads = []
    for name, files in (("one", ["first-alpha.txt", "first-beta.txt"]), ("two", ["second-gamma.txt"])):
        (tmp_path / name).mkdir()
        for f in files:
            (tmp_path / name / f).write_text(f"Notes in {f}: tomatoes along the south fence.", encoding="utf-8")
        uploads.append([str(tmp_path / name / f) for f in files])
    options = {"url": served, "browser": BROWSER, "uploads": uploads}
    out = subprocess.run([NODE, str(DRIVER.parent / "report_check.mjs"), json.dumps(options)], capture_output=True,
                         text=True, encoding="utf-8", timeout=240)
    state = json.loads(out.stdout)
    assert "error" not in state, state.get("error")
    first, second = state["reports"]
    assert first["shown"] and "2 files received" in first["text"] and "first-alpha" in first["text"]
    assert second["shown"] and "1 file received" in second["text"] and "second-gamma" in second["text"]
    assert "first-alpha" not in second["text"] and first["upload"] != second["upload"]
