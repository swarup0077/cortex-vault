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
EXPIRY_S = 6  # the "demo-expiry" app's session lifetime
needs_browser = pytest.mark.skipif(not (BROWSER and NODE), reason="needs Node 22+ and Edge, Chrome or Chromium")


def _context(mode: str, tmp: Path):
    from vault.library import Library
    from vault.session import SessionManager
    from vault.ui import views
    from vault.ui.layout import UiContext

    from gradio.utils import get_upload_folder

    demo = REPO_ROOT / "demo_index"
    if mode.startswith("demo"):  # "demo-expiry": sessions end EXPIRY_S after the last search or upload
        cfg = Config(data_dir=demo, **({"session_ttl_s": EXPIRY_S} if mode == "demo-expiry" else {}))
        lib = Library(cfg, read_only=True, base_dir=REPO_ROOT)
        sessions = SessionManager(lib, cfg, tmp / "sessions")
        if mode == "demo-expiry":
            sessions.start_reaper(interval_s=1)
        # Gradio's own upload folder, as app.py: a session reads only files from there
        return UiContext("demo", lib, suggestions=views.DEMO_SUGGESTIONS, upload_dir=Path(get_upload_folder()),
                         sessions=sessions)
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
    application = make_app(build_blocks(ctx), ctx.resolve, demo=ctx.demo, sessions=ctx.sessions,
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
    words = lambda r: " ".join(r["text"].split()).replace(".txtadded", ".txt added")  # noqa: E731
    assert "first-alpha.txt added" in words(first) and "second-gamma.txt added" in words(second)  # kept, not refused


@needs_browser
@pytest.mark.parametrize("served", ["demo"], indirect=True)
def test_the_archive_follows_the_scope_in_a_browser(served, tmp_path) -> None:
    """Owner, 20 Oct (v1.0.3): The Archive's total and per-type counts, and the phone's filter chips (the same
    buttons), follow "Search in"; they update on a scope switch, and after an upload without the results moving.
    The results and their "N matches" line follow the scope too."""
    from vault.library import Library

    samples = Library(Config(data_dir=REPO_ROOT / "demo_index"), read_only=True, base_dir=REPO_ROOT).type_counts()
    n = sum(samples.values())
    batches = []
    for name, files in (("first", ["quokka-1.txt", "quokka-2.txt", "quokka-3.txt"]),
                        ("second", ["later-1.txt", "later-2.txt"])):
        (tmp_path / name).mkdir()
        for f in files:
            (tmp_path / name / f).write_text(f"quokka field notes from the {f} survey" if name == "first"
                                             else f"plain text {f}", encoding="utf-8")
        batches.append([str(tmp_path / name / f) for f in files])

    def run(**more) -> dict:
        options = {"url": served, "browser": BROWSER, "uploads": batches, "query": "quokka field notes", **more}
        out = subprocess.run([NODE, str(DRIVER.parent / "scope_check.mjs"), json.dumps(options)], capture_output=True,
                             text=True, encoding="utf-8", timeout=300)
        state = json.loads(out.stdout)
        assert "error" not in state, state.get("error")
        return state

    def expect(extra_text: int, only_uploads: bool = False) -> dict:
        types = dict.fromkeys(samples, 0) if only_uploads else dict(samples)
        types["text"] += extra_text
        return {"all": sum(types.values()), **types}

    s = run()
    assert s["start"]["scope"] == "Both" and s["start"]["total"] == n and s["start"]["types"] == expect(0)
    assert s["afterFirst"]["scope"] == "My uploads" and s["afterFirst"]["total"] == 3  # home switches to My uploads
    assert s["scopes"]["Samples"]["total"] == n and s["scopes"]["Samples"]["types"] == expect(0)
    assert s["scopes"]["My uploads"]["total"] == 3 and s["scopes"]["My uploads"]["types"] == expect(3, True)
    assert s["scopes"]["Both"]["total"] == n + 3 and s["scopes"]["Both"]["types"] == expect(3)
    mine, theirs, both = (s["results"][k] for k in ("My uploads", "Samples", "Both"))
    assert mine["matches"] == 3 and mine["cards"] and all(c.startswith("quokka-") for c in mine["cards"])
    assert not any(c.startswith("quokka-") for c in theirs["cards"])
    assert sum(c.startswith("quokka-") for c in both["cards"]) == 3 and both["matches"] >= 3
    assert (mine["total"], theirs["total"], both["total"]) == (3, n, n + 3)
    before, after = s["afterSecond"]["before"], s["afterSecond"]["after"]
    assert after["render"] == before["render"] and after["cards"] == before["cards"]  # the results didn't move
    assert after["scope"] == "Both" and after["total"] == n + 5 and after["types"] == expect(5)
    phone = run(phone=True)  # another visitor, on a phone: no Search in, the scope is Both
    assert phone["afterFirst"]["scope"] == "Both" and not phone["afterFirst"]["scopeShown"]
    assert phone["afterFirst"]["chips"] == "flex" and phone["afterFirst"]["total"] == n + 3
    assert phone["afterFirst"]["types"] == expect(3)


@needs_browser
@pytest.mark.parametrize("served", ["demo"], indirect=True)
def test_delete_my_uploads_in_a_browser(served, tmp_path) -> None:
    """Owner, 20 Oct (v1.0.4): the button shows once there are uploads, asks first ("Cancel" changes nothing),
    then wipes the session: the toast, back to Samples with My uploads 0, the button gone, the files off the disk,
    and the uploads no longer found. On a phone too, where the button follows the results."""
    from vault.library import Library

    n = sum(Library(Config(data_dir=REPO_ROOT / "demo_index"), read_only=True, base_dir=REPO_ROOT).type_counts().values())
    (tmp_path / "up").mkdir()
    files = []
    for i in (1, 2, 3):
        f = tmp_path / "up" / f"quokka-{i}.txt"
        f.write_text(f"quokka field notes from survey {i}", encoding="utf-8")
        files.append(str(f))
    sessions = tmp_path / "sessions"  # the served app's session folder
    for phone in (False, True):
        options = {"url": served, "browser": BROWSER, "files": files, "query": "quokka field notes", "phone": phone}
        out = subprocess.run([NODE, str(DRIVER.parent / "delete_check.mjs"), json.dumps(options)], capture_output=True,
                             text=True, encoding="utf-8", timeout=300)
        s = json.loads(out.stdout)
        assert "error" not in s, s.get("error")
        what = "phone" if phone else "desktop"
        assert not s["start"]["deleteShown"] and s["uploaded"]["deleteShown"] and s["uploaded"]["myUploads"] == 3, what
        assert sum(c.startswith("quokka-") for c in s["searched"]["cards"]) == 3, what
        assert s["dialogs"][0] == {"type": "confirm", "message": "Delete all 3 files you uploaded? This can't be undone."}
        assert s["cancelled"]["deleteShown"] and s["cancelled"]["myUploads"] == 3, what  # Cancel: nothing happened
        d = s["deleted"]
        assert len(s["dialogs"]) == 2 and d["toast"] == "Your 3 uploads were deleted", what
        assert d["scope"] == "Samples" and d["total"] == n and d["myUploads"] == 0 and not d["deleteShown"], what
        assert not any(c.startswith("quokka-") for c in d["cards"]), what  # the query, run again in Samples
        assert not any(c.startswith("quokka-") for c in s["searchedAgain"]["cards"]), what
        assert not [p for p in sessions.iterdir() if p.is_dir()], what  # the session's folder is gone


@needs_browser
@pytest.mark.parametrize("served", ["demo"], indirect=True)
def test_uploads_survive_a_reload_per_browser(served, tmp_path) -> None:
    """Owner (v1.1.0): a reload and a second tab keep the uploads, the counts and the last upload report; another
    browser sees none, and a forged cookie gets nothing and is cleared. Opening and searching set no cookie; the
    first accepted upload sets an HttpOnly, Secure, SameSite=Strict session cookie that the page's script can't
    read."""
    (tmp_path / "up").mkdir()
    files = []
    for i in (1, 2):
        f = tmp_path / "up" / f"quokka-{i}.txt"
        f.write_text(f"quokka field notes from survey {i}", encoding="utf-8")
        files.append(str(f))

    def run(**more) -> dict:
        options = {"url": served, "browser": BROWSER, "files": files, "query": "quokka field notes", **more}
        out = subprocess.run([NODE, str(DRIVER.parent / "session_check.mjs"), json.dumps(options)],
                             capture_output=True, text=True, encoding="utf-8", timeout=300)
        state = json.loads(out.stdout)
        assert "error" not in state, state.get("error")
        return state

    s = run()
    mine = lambda state: sorted(c for c in state["cards"] if c.startswith("quokka-"))  # noqa: E731
    assert s["start"]["cookie"] is None and s["searched"]["cookie"] is None  # opening and searching: no cookie
    cookie = s["uploaded"]["cookie"]
    assert cookie == {"httpOnly": True, "secure": True, "sameSite": "Strict", "session": True, "path": "/",
                      "length": cookie["length"]} and cookie["length"] >= 22  # >= 128 random bits
    assert "cv_session" not in s["uploaded"]["docCookie"] and s["uploaded"]["myUploads"] == 2
    for step in ("reloaded", "secondTab"):
        r = s[step]
        assert r["myUploads"] == 2 and r["scope"] == "My uploads" and r["total"] == 2, step
        assert r["bar"] == "Upload done: 2 added" and r["report"], step  # the last upload report is back
        assert sorted(r["recent"])[:2] == ["quokka-1.txt", "quokka-2.txt"], step
        assert mine(s[step + "Search"]) == ["quokka-1.txt", "quokka-2.txt"], step
    for step in ("other", "forged"):
        r = s[step]
        assert r["myUploads"] == 0 and r["scope"] == "Both" and r["bar"] is None and r["cookie"] is None, step
        assert r["toast"] is None and not mine(s[step + "Search"]), step
    assert s["forgedSet"] is not None  # the forged cookie was there, and the page cleared it
    phone = run(phone=True)  # a phone: no Search in, the scope stays Both, and the uploads are back
    for step in ("reloaded", "secondTab"):
        r = phone[step]
        assert r["myUploads"] == 2 and r["scope"] == "Both" and r["bar"] == "Upload done: 2 added", step
        assert mine(phone[step + "Search"]) == ["quokka-1.txt", "quokka-2.txt"], step
    assert phone["other"]["myUploads"] == 0 and not mine(phone["otherSearch"])


@needs_browser
@pytest.mark.parametrize("served", ["demo-expiry"], indirect=True)
def test_an_expired_session_is_gone_after_a_reload(served, tmp_path) -> None:
    """A reload after the session expired: the uploads are gone, the page says so once, and the cookie is cleared."""
    from vault.ui import views

    f = tmp_path / "quokka.txt"
    f.write_text("quokka field notes", encoding="utf-8")
    options = {"url": served, "browser": BROWSER, "files": [str(f)], "expiry": EXPIRY_S + 3}
    out = subprocess.run([NODE, str(DRIVER.parent / "session_check.mjs"), json.dumps(options)], capture_output=True,
                         text=True, encoding="utf-8", timeout=300)
    s = json.loads(out.stdout)
    assert "error" not in s, s.get("error")
    assert s["uploaded"]["cookie"] is not None and s["uploaded"]["myUploads"] == 1
    assert s["toast"] == views.expired_text(1)  # "Your earlier uploads were deleted after 1 minute"
    e = s["expired"]
    assert e["myUploads"] == 0 and e["cookie"] is None and e["bar"] is None and "quokka.txt" not in e["recent"]
    assert not [p for p in (tmp_path / "sessions").iterdir() if p.is_dir()]


@needs_browser
@pytest.mark.parametrize("served", ["demo"], indirect=True)
def test_files_dropped_anywhere_are_added(served, tmp_path) -> None:
    """Owner (v1.1.0): dragging files anywhere over the page shows a full-page overlay with their number, and
    dropping them anywhere adds them, on the home page and on the results page; a cancelled drag adds nothing. The
    home drop zone is bigger, with only the privacy notice under it. A phone has no dragging: the zone says Add your
    own files and opens the picker."""
    from vault.ui import views

    lists = []
    for name, files in (("first", ["quokka-a1.txt", "quokka-a2.txt"]), ("second", ["quokka-b1.txt"])):
        (tmp_path / name).mkdir()
        for f in files:
            (tmp_path / name / f).write_text(f"quokka field notes {f}", encoding="utf-8")
        lists.append([str(tmp_path / name / f) for f in files])

    def run(**more) -> dict:
        options = {"url": served, "browser": BROWSER, "files": lists, "query": "quokka field notes", **more}
        out = subprocess.run([NODE, str(DRIVER.parent / "drop_check.mjs"), json.dumps(options)], capture_output=True,
                             text=True, encoding="utf-8", timeout=300)
        state = json.loads(out.stdout)
        assert "error" not in state, state.get("error")
        return state

    s = run()
    zone = s["home"]["zone"]
    assert zone["height"] >= 200 and zone["text"].startswith("Drop files anywhere on this page |")
    assert "Add your own files" not in zone["text"] and zone["text"].endswith("Choose files")
    assert zone["next"] == "cv-note cv-drop__note" and zone["nextText"] == views.Mode(demo=True).upload_notice
    assert s["home"]["overlay"] is None
    first = s["first"]
    assert first["during"]["overlay"] == "Drop to add 2 files" and first["dropped"]["overlay"] is None
    assert first["after"]["myUploads"] == 2 and first["after"]["stage"] == "done"
    assert sorted(s["results"]["cards"]) == ["quokka-a1.txt", "quokka-a2.txt"]  # home switched to My uploads
    assert s["cancelDuring"]["overlay"] == "Drop to add 1 file" and s["cancelled"]["overlay"] is None
    assert s["cancelled"]["myUploads"] == 2 and s["cancelled"]["upload"] == first["after"]["upload"]  # nothing sent
    second = s["second"]
    assert second["during"]["overlay"] == "Drop to add 1 file" and second["during"]["view"] == "results"
    assert second["after"]["myUploads"] == 3 and second["after"]["render"] == s["results"]["render"]  # results stay
    phone = run(phone=True)["home"]["zone"]
    assert phone["text"].startswith("Add your own files |") and "Drop files anywhere" not in phone["text"]
