"""The Cortex Vault UI (SPEC §8): server-built HTML, the numbered list, The Archive, the id-only routes, demo
safety, no query logging, and the handlers, without a browser and without real models."""

import logging
import re
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from conftest import build_pdf, camera_exif, save_image
from vault.config import Config
from vault.demo import build_demo
from vault.library import Library, Recent
from vault.search.engine import SearchResponse, SearchResult
from vault.search.parse import ParsedQuery
from vault.session import FileStatus, SessionManager, UploadResult
from vault.ui import layout, views
from vault.ui.layout import UiContext, build_blocks
from vault.ui.server import HEAD, STATIC, make_app

EVIL = "<img src=x onerror=alert(1)>.png"
DEMO = views.Mode(demo=True)
LOCAL = views.Mode(demo=False)


def place(path: str) -> str:
    return str(Path(path).parent)


def result(i: int, **kw) -> SearchResult:
    base = dict(file_id=i, kind="screenshot", filename=f"shot {i}.png", folder="C:/shots",
                paths=[f"C:/shots/shot {i}.png"], copies=0, effective_date="2026-10-12T10:15:30", page=None,
                chunk_id=100 + i, snippet_html="access <mark>denied</mark>", ranks={"bm25": i, "dense": i + 1},
                rrf_score=1 / (60 + i), thumb_url=f"thumbs/{'ab' * 32}.webp", shares={"bm25": 1 / (60 + i)},
                width=1920, height=1080, size_bytes=214_000, chunk_source="ocr", text_sources=["ocr"])
    return SearchResult(**{**base, **kw})


def visual(i: int, **kw) -> SearchResult:
    return result(i, kind="photo", ranks={"clip": 1}, snippet_html="", chunk_id=None, chunk_source=None,
                  text_sources=[], **kw)


def response(results, weaker=()) -> SearchResponse:
    return SearchResponse("q", ParsedQuery("q"), list(results), {"bm25": 120.0, "dense": 90.0},
                          clip={"candidates": 24, "kept": 1, "strong": 1, "gated": True, "weight": 1.0,
                                "visual": False}, weaker=list(weaker))


# ---------------------------------------------------------------- escaping


def test_a_hostile_file_name_shows_as_plain_text_everywhere() -> None:
    r = result(1, filename=EVIL, paths=[f"C:/a/{EVIL}"])
    pages = [
        views.results(response([r], weaker=[result(2, filename=EVIL, weaker=True)]), DEMO, place, query=EVIL),
        views.results(response([visual(3, filename=EVIL)]), DEMO, place, query="q"),
        views.upload_report([FileStatus(EVIL, "indexed"), FileStatus(EVIL, "rejected", EVIL)], DEMO, received=2),
        views.home(DEMO, views.Archive(), recent=[Recent(1, EVIL, "photo", "2026-10-18T10:00:00")]),
        views.empty(response([]), DEMO, query=EVIL),
    ]
    for page in pages:
        assert "<img src=x" not in page and "onerror=alert(1)>" not in page
        assert "&lt;img src=x onerror=alert(1)&gt;" in page


def test_snippets_keep_only_mark() -> None:
    r = result(1, snippet_html="&lt;script&gt; <mark>denied</mark>")
    page = views.results(response([r]), DEMO, place, query="denied")
    assert "<script" not in page and "<mark>denied</mark>" in page


# ---------------------------------------------------------------- the numbered list


def option_ids(page: str) -> list[str]:
    return re.findall(r'role="option" id="cv-r-(\d+)"', page)


def detail_url(page: str, file_id: int) -> str:
    return re.search(rf'id="cv-r-{file_id}"[^>]*data-detail="([^"]*)"', page).group(1)


def test_one_numbered_list_in_fused_order_with_the_weaker_after_the_divider() -> None:
    """Owner's rule (SPEC §8): one list, 01, 02…, a picture-only match in its fused place, the weaker matches after
    a thin divider with their numbers continuing."""
    page = views.results(response([result(1), visual(2), result(3)], weaker=[result(4, weaker=True)]), DEMO, place,
                         query="q")
    assert option_ids(page) == ["1", "2", "3", "4"]  # also the arrow keys' order
    assert re.findall(r'cv-card__rank">(\d+)<', page) == ["01", "02", "03", "04"]
    assert re.findall(r'data-group="(\w+)"', page) == ["matches", "weaker"]
    divider = page.index("Weaker matches")
    assert page.index('id="cv-r-3"') < divider < page.index('id="cv-r-4"')
    assert re.findall(r'id="cv-r-(\d+)"[^>]*data-weaker', page) == ["4"]


def test_the_count_line_counts_the_weaker_matches_separately() -> None:
    page = views.results(response([result(i) for i in range(1, 15)],
                                  weaker=[result(i, weaker=True) for i in range(15, 51)]), DEMO, place, query="q")
    assert "data-count-n>14<" in page and "matches · 36 weaker" in page and "in 0.21 s" in page
    assert views.count_words(1, 0) == "match" and views.count_words(3, 2, "Photos") == "matches in Photos · 2 weaker"
    assert "data-clear-filter" in page  # the × that clears a type filter (shown by cortex.js)


def test_show_n_more_counts_what_is_left_after_twenty() -> None:
    page = views.results(response([result(i) for i in range(1, 31)],
                                  weaker=[result(i, weaker=True) for i in range(31, 51)]), DEMO, place, query="q")
    assert "Show 30 more" in page and page.count("data-more hidden") == 30
    assert re.search(r'data-group="weaker" hidden', page)  # all 20 weaker are past the first 20
    assert "data-more-btn" not in views.results(response([result(i) for i in range(1, 6)]), DEMO, place, query="q")


def test_a_row_says_where_how_the_text_was_read_its_size_and_found_by() -> None:
    pdf = result(1, kind="pdf", filename="IT_helpdesk_guide.pdf", page=4, pages=12, size_bytes=380_000,
                 chunk_source="pdf_text", ranks={"dense": 1, "bm25": 2}, width=None, height=None)
    assert views.row_meta(pdf) == ["page 4 of 12", "PDF text", "371 KB"]
    assert views.row_meta(result(1)) == ["OCR", "1920 × 1080 · 209 KB"]
    assert views.row_meta(visual(1)) == ["no text", "1920 × 1080 · 209 KB"]
    row = views.row(pdf, "q", DEMO, None)
    assert "meaning <b>#1</b>" in row and "exact words <b>#2</b>" in row and "12 Oct 2026" in row
    assert views.found_by_rows(result(1, ranks={"bm25": 1, "all_terms": 1, "dense": 2, "clip": None})) == \
        [("bm25", 1), ("all_terms", 1), ("dense", 2)]


def test_picture_only_matches_show_their_thumbnail_and_get_no_boxes() -> None:
    """SPEC §8: "Picture match", and the detail request carries neither the query nor a chunk, so no box is drawn.
    A screenshot found by its text says "Matched text" and asks for boxes on that text."""
    page = views.results(response([result(1), visual(2)]), DEMO, place, query="red error")
    text_row, pic_row = page.split('id="cv-r-1"')[1].split('id="cv-r-2"')
    assert "Matched text" in text_row and "Picture match" not in text_row
    assert "Picture match" in pic_row.split('role="option"')[0] and "/thumb/2" in pic_row
    assert "q=" not in detail_url(page, 2) and "chunk=" not in detail_url(page, 2)
    assert "q=red+error" in detail_url(page, 1) and "chunk=101" in detail_url(page, 1)


def test_the_archive_shows_index_counts_per_type() -> None:
    archive = views.Archive({"screenshot": 18, "pdf": 10, "text": 3, "photo": 16, "doc": 2, "slides": 0})
    page = views.results(response([result(1)]), DEMO, place, archive, query="q")
    counts = dict(re.findall(r'data-filter="(\w+)"[^>]*>.*?<span class="cv-filter__n">(\d+)', page))
    assert counts == {"all": "49", "screenshot": "18", "pdf": "10", "text": "3", "photo": "16", "doc": "2",
                      "slides": "0"}
    assert re.search(r'data-filter="slides"[^>]*disabled', page) and "49 files" in page
    assert "Search in" in page and 'data-act="add"' in page and 'data-act="folders"' not in page
    local = views.results(response([result(1)]), LOCAL, place, archive, query="q")
    assert "Search in" not in local and 'data-act="folders"' in local and 'data-act="add"' not in local


def test_type_of_splits_plain_text_from_documents() -> None:
    assert views.type_of("doc", "notes.md") == "text" and views.type_of("doc", "a.TXT") == "text"
    assert views.type_of("doc", "a.docx") == "doc" and views.type_of("pdf", "a.pdf") == "pdf"


# ---------------------------------------------------------------- the detail view


def test_detail_rows_show_each_method_with_its_real_rank() -> None:
    r = result(1, ranks={"bm25": 1, "dense": 37}, weaker=True)
    tpl = views.preview_template(r, response([r]), DEMO, place, None, 15)
    assert "Result 15 / Screenshot" in tpl
    assert re.findall(r'cv-detail__method"><span>([^<]+)</span><b>rank (\d+)', tpl) == [("exact words", "1"),
                                                                                       ("meaning", "37")]
    assert "Download" in tpl and "Extracted text" in tpl and "OCR, on this server" in tpl
    assert "Open" in views.preview_template(r, response([r]), LOCAL, place, None, 1)


def test_details_show_the_rrf_shares_and_the_gate() -> None:
    r = result(1, ranks={"bm25": 1, "dense": 2}, shares={"bm25": 1 / 61, "dense": 1 / 62}, rrf_score=1 / 61 + 1 / 62)
    page = views.details(r, response([r]))
    assert "RRF share 0.0164" in page and "RRF share 0.0161" in page and "0.0325" in page
    assert "z-score over 24 images; kept 1 (1 strong)" in page and "<details" in page and " open" not in page


def test_text_read_by_and_size() -> None:
    assert views.text_read_by(result(1), DEMO) == "OCR, on this server"
    assert views.text_read_by(result(1), LOCAL) == "OCR, on this computer"
    assert views.text_read_by(result(1, kind="pdf", chunk_source="pdf_text"), DEMO) == "The PDF's own text layer"
    assert views.text_read_by(result(1, kind="doc", filename="a.docx", chunk_source="doc_text"), DEMO) == \
        "Word document text"
    assert views.text_read_by(visual(1), DEMO) == "No text found"
    assert views.size_text(result(1)) == "1920 × 1080 · 209 KB"
    assert views.size_text(result(1, kind="pdf", pages=12, size_bytes=380_000)) == "12 pages · 371 KB"
    assert views.size_text(result(1, kind="slides", pages=1, size_bytes=2_200_000)) == "1 slide · 2.1 MB"


def test_copy_link_and_every_url_carry_ids_never_paths() -> None:
    r = result(7, paths=["C:/Users/me/secret folder/shot 7.png"])
    page = views.results(response([r]), DEMO, place, query="access denied", session=str(uuid.uuid4()))
    assert re.search(r'data-link="([^"]+)"', page).group(1) == "?q=access+denied&amp;r=7"
    for attr in ("data-link", "data-open", "data-detail", "src"):
        for value in re.findall(rf'{attr}="([^"]*)"', page):
            assert "secret" not in value and "Users" not in value and "C:" not in value


# ---------------------------------------------------------------- wording per mode


def test_the_upload_lifetime_comes_from_the_config(tmp_path, fake_embedder) -> None:
    """Owner's note (19 Oct): every text that says when uploads are deleted takes the minutes from session_ttl_s."""
    cfg = Config(data_dir=tmp_path / "demo", session_ttl_s=45 * 60)
    lib = Library(cfg)
    ctx = UiContext("demo", lib, sessions=SessionManager(lib, cfg, tmp_path / "sessions"))
    texts = [views.footer(ctx.view_mode), layout.home_html(ctx, None),
             views.upload_report([], ctx.view_mode, received=0)]
    for text in texts:
        assert "45 minutes after your last search or upload" in text and "60 minutes" not in text
    assert "Demo server · no cloud services" in views.top(ctx.view_mode)
    local = views.Mode(demo=False)
    assert "Local only · nothing leaves this device" in views.top(local)
    assert "Nothing leaves this device" in views.footer(local) and "minutes" not in views.footer(local)


# ---------------------------------------------------------------- nothing served loads from the internet


def test_no_served_html_css_or_js_contains_an_external_url() -> None:
    served = [p for p in STATIC.rglob("*") if p.suffix in (".css", ".js", ".svg", ".html")]
    assert {p.suffix for p in served} >= {".css", ".js", ".svg"}
    texts = [p.read_text(encoding="utf-8") for p in served]
    texts += [HEAD, views.top(DEMO, views.DEMO_SUGGESTIONS), views.top(LOCAL), views.footer(DEMO), views.sprite()]
    texts.append(views.results(response([result(1), visual(2)], weaker=[result(3, weaker=True)]), DEMO, place,
                               query="q"))
    texts.append(views.home(DEMO, views.Archive(), recent=[], samples=52))
    for text in texts:
        urls = re.findall(r"https?://[^\s\"')<>]+", text)
        assert [u for u in urls if u != "http://www.w3.org/2000/svg"] == []
    assert not (STATIC / "fonts").exists()  # system fonts only (SPEC §8)


def test_the_theme_names_plain_system_fonts() -> None:
    from vault.ui.server import theme

    fonts = str(theme().font)
    assert "Arial" in fonts and "Geist" not in fonts and "GoogleFont" not in fonts


# ---------------------------------------------------------------- routes and demo safety


@pytest.fixture
def demo_ctx(tmp_path, fake_embedder) -> UiContext:
    base = tmp_path / "repo"
    build_pdf(base / "corpus" / "papers" / "diffusion.pdf", ["Denoising diffusion models reverse a noising process."])
    save_image(Image.new("RGB", (640, 480), (220, 30, 30)), base / "corpus" / "photos" / "IMG_red.jpg",
               exif=camera_exif())
    build_demo(base / "corpus", base / "demo_index", Config(data_dir=tmp_path / "unused"), base=base)
    cfg = Config(data_dir=base / "demo_index")
    demo = Library(cfg, read_only=True, base_dir=base)
    (tmp_path / "gradio").mkdir()
    return UiContext("demo", demo, sessions=SessionManager(demo, cfg, tmp_path / "sessions"),
                     upload_dir=tmp_path / "gradio", poll_s=0.01, suggestions=views.DEMO_SUGGESTIONS)


def gradio_file(ctx: UiContext, name: str, text: str) -> str:
    d = ctx.upload_dir / uuid.uuid4().hex
    d.mkdir()
    (d / name).write_text(text, encoding="utf-8")
    return str(d / name)


def upload(ctx: UiContext, files: dict[str, str], sid=None):
    """→ (the last status, the session id): the upload bar's last word on this upload."""
    *_, (status, sid) = layout.handle_upload(ctx, sid, [gradio_file(ctx, n, t) for n, t in files.items()])
    return status, sid


def test_file_route_sends_attachments_and_refuses_anything_path_like(demo_ctx) -> None:
    client = TestClient(make_app(build_blocks(demo_ctx), demo_ctx.resolve, demo=True))
    pdf = demo_ctx.library.search("diffusion").results[0]
    r = client.get(f"/file/{pdf.file_id}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("attachment") and "diffusion.pdf" in \
        r.headers["content-disposition"]
    for bad in ("/file/999999", "/file/-1", "/file/1.0", "/file/1e3", "/file/%2e%2e", "/file/C:%5Cwindows",
                "/file/..%2F..%2Fvault.db", "/file/1?s=not-a-session", f"/file/1?s={uuid.uuid4()}"):
        assert client.get(bad).status_code == 404, bad


def test_another_sessions_upload_is_a_404_not_a_403(demo_ctx) -> None:
    """Session A has two uploads, session B one: A's second file id doesn't exist in B's index."""
    client = TestClient(make_app(build_blocks(demo_ctx), demo_ctx.resolve, demo=True))
    _, a = upload(demo_ctx, {"one.txt": "Robotics club budget.", "two.txt": "Robotics club minutes, October."})
    _, b = upload(demo_ctx, {"other.txt": "A recipe for flatbread."})
    lib_a = demo_ctx.session(a).library
    ids_a = {r.filename: r.file_id for r in lib_a.search("robotics club", roots=[demo_ctx.session(a).root]).results}
    with demo_ctx.session(b).library.connect() as conn:
        ids_b = {r[0] for r in conn.execute("SELECT id FROM files")}
    only_a = [i for i in ids_a.values() if i not in ids_b]
    assert only_a, "the test needs a file id that only session A has"
    for fid in only_a:
        assert client.get(f"/file/{fid}?s={a}").status_code == 200
        for route in ("file", "thumb", "preview", "detail"):
            assert client.get(f"/{route}/{fid}?s={b}").status_code == 404
            assert client.get(f"/{route}/{fid}").status_code == 404  # and not without a session either
    for fid in ids_a.values():  # an id both have is B's own file through B's session: never A's content
        r = client.get(f"/file/{fid}?s={b}")
        assert r.status_code == 404 or b"Robotics" not in r.content


def test_detail_route_returns_boxes_and_text_as_json(demo_ctx) -> None:
    client = TestClient(make_app(build_blocks(demo_ctx), demo_ctx.resolve, demo=True))
    hit = demo_ctx.library.search("denoising diffusion").results[0]
    data = client.get(f"/detail/{hit.file_id}?chunk={hit.chunk_id}&page=1&q=denoising+diffusion").json()
    assert data["text"].startswith("Denoising diffusion") and data["boxes"]
    assert all(set(b) == {"x", "y", "w", "h", "kind"} for b in data["boxes"])
    assert client.get("/detail/abc").status_code == 404


def test_local_mode_has_no_download_route(tmp_path, fake_embedder, make_search_corpus) -> None:
    lib = Library(Config(data_dir=tmp_path / "data"))
    lib.index([make_search_corpus(tmp_path / "files")])
    ctx = UiContext("local", lib)
    client = TestClient(make_app(build_blocks(ctx), ctx.resolve, demo=False))
    assert client.get("/file/1").status_code == 404
    assert client.get("/cv/cortex.js").status_code == 200 and client.get("/favicon.ico").status_code == 200


def test_the_folders_panel_exists_only_in_local_mode(demo_ctx, tmp_path, fake_embedder) -> None:
    def ids_and_handlers(blocks):
        ids = {getattr(b, "elem_id", None) for b in blocks.blocks.values()}
        names = {getattr(f.fn, "__name__", "") for f in blocks.fns.values()}
        return ids, names

    ids, names = ids_and_handlers(build_blocks(demo_ctx))
    assert "cv-folders" not in ids
    assert not names & {"on_add", "on_index", "on_remove", "on_top_click", "on_main_click"}
    assert "on_upload" in names
    ids, names = ids_and_handlers(build_blocks(UiContext("local", Library(Config(data_dir=tmp_path / "local")))))
    assert "cv-folders" in ids and {"on_add", "on_index", "on_remove"} <= names and "on_upload" not in names


# ---------------------------------------------------------------- handlers


def test_search_handler_home_results_and_empty(demo_ctx) -> None:
    home = layout.run_search(demo_ctx, None, "  ")
    assert 'data-view="home"' in home and "2 files" in home and "IMG_red.jpg" in home
    assert "bayes theorem" in views.top(demo_ctx.view_mode, demo_ctx.suggestions)  # the example searches
    page = layout.run_search(demo_ctx, None, "  denoising   diffusion ")
    assert 'data-query="denoising diffusion"' in page and "diffusion.pdf" in page
    nonsense = layout.run_search(demo_ctx, None, "zzzqqq xylophone")
    assert "No close matches for “zzzqqq xylophone”" in nonsense and "Weaker matches" in nonsense
    assert "Nothing uploaded yet" in layout.run_search(demo_ctx, None, "diffusion", "My uploads")


def test_upload_report_then_scopes(demo_ctx) -> None:
    status, sid = upload(demo_ctx, {"minutes.txt": "Minutes: the diffusion reading group meets Friday."})
    assert sid and 'data-stage="done"' in status and 'data-summary="1 added"' in status and "minutes.txt" in status
    assert "1 file received." in status and views._e(demo_ctx.view_mode.upload_notice) in status
    assert 'data-index="3 files · up to date"' in status and 'data-new-session="true"' in status
    assert layout.archive(demo_ctx, sid).counts["text"] == 1  # The Archive counts the upload with the samples
    mine = layout.run_search(demo_ctx, sid, "diffusion", "My uploads")
    samples = layout.run_search(demo_ctx, sid, "diffusion", "Samples")
    assert "minutes.txt" in mine and "diffusion.pdf" not in mine.split("Weaker matches")[0]
    assert "diffusion.pdf" in samples and "minutes.txt" not in samples and f"s={sid}" in mine


def test_the_bar_reports_listing_then_reading_with_real_counts(demo_ctx, monkeypatch) -> None:
    """The upload bar's server stages (SPEC §8): "Listing" as soon as the server has the files (no count until it
    knows one), the per-file report once they're listed, "Reading N of M" with real counts, then done. The upload
    never renders the main part, so results on screen don't move."""
    def slow(session, files, upload_dir, progress, on_listed=None):
        statuses = [FileStatus(Path(f).name, "indexed") for f in files]
        for i, f in enumerate(files):
            progress(i, len(files), str(f), phase="list")
        on_listed(UploadResult(None, statuses))
        for i in range(len(files)):
            progress(i, len(files), str(files[i]), phase="read")
            time.sleep(0.05)
        return None, UploadResult(None, statuses)

    monkeypatch.setattr(demo_ctx.sessions, "add_files", slow)
    steps = list(layout.handle_upload(demo_ctx, None, [gradio_file(demo_ctx, f"n{i}.txt", "x") for i in range(3)]))
    assert all(len(step) == 2 for step in steps)  # (status, session): never the main part
    stages = [re.search(r'data-stage="(\w+)"', status).group(1) for status, _ in steps]
    assert stages[0] == "list" and 'data-total="0"' in steps[0][0] and stages[-1] == "done"
    assert stages.index("read") > 0 and "list" not in stages[stages.index("read"):]
    first_report = next(i for i, (status, _) in enumerate(steps) if "<template data-report>" in status)
    assert first_report < len(steps) - 1  # shown while the files are still read
    reading = [status for status, _ in steps if 'data-stage="read"' in status]
    assert any('data-done="1" data-total="3"' in r and 'data-dial="Reading 2 of 3"' in r for r in reading)
    assert all('data-busy="true"' in r for r in reading)
    assert 'data-busy="false"' in steps[-1][0] and 'data-summary="3 added"' in steps[-1][0]


def test_a_search_runs_and_returns_while_an_upload_is_being_read(demo_ctx, monkeypatch) -> None:
    """Owner's rule (SPEC §8): the bar never blocks the page. Phase 2 is held on its first file while a search for
    the uploaded file runs: it returns, and finds the file by its name and native text (listed in phase 1)."""
    reading, searched = threading.Event(), threading.Event()
    real = demo_ctx.sessions.add_files

    def held(session, files, upload_dir, progress=None, on_listed=None):
        def hold(done, total, path, phase="read"):
            if progress:
                progress(done, total, path, phase=phase)
            if phase == "read" and not reading.is_set():
                reading.set()
                assert searched.wait(30)
        return real(session, files, upload_dir, hold, on_listed)

    monkeypatch.setattr(demo_ctx.sessions, "add_files", held)
    steps: list = []
    path = gradio_file(demo_ctx, "robotics minutes.txt", "Robotics club minutes: the servo budget was approved.")
    worker = threading.Thread(target=lambda: steps.extend(layout.handle_upload(demo_ctx, None, [path])))
    worker.start()
    try:
        assert reading.wait(30), "phase 2 never started"
        sid = next(iter(demo_ctx.sessions.sessions))
        started = time.monotonic()
        page = layout.run_search(demo_ctx, sid, "servo budget", "My uploads")
        assert "robotics minutes.txt" in page and time.monotonic() - started < 10
    finally:
        searched.set()
        worker.join(30)
    assert any('data-stage="read"' in status for status, _ in steps) and 'data-stage="done"' in steps[-1][0]


def test_results_know_their_index_version_and_update_keeps_the_selection(demo_ctx) -> None:
    """"Update results" (SPEC §8): results carry the index version they were found in; listing and reading a file
    each raise it, so the bar can tell the results are older. Re-running the search with `select` keeps the
    selected result."""
    before = layout.run_search(demo_ctx, None, "denoising diffusion")
    v0 = int(re.search(r'data-version="(\d+)"', before).group(1))
    status, sid = upload(demo_ctx, {"diffusion notes.txt": "Notes on denoising diffusion models."})
    v1 = int(re.search(r'data-version="(\d+)"', status).group(1))
    assert v1 == v0 + 2  # one file listed, one file read
    hit = demo_ctx.library.search("denoising diffusion").results[0]
    again = layout.run_search(demo_ctx, sid, "denoising diffusion", "Both", select=hit.file_id)
    assert f'data-select="{hit.file_id}"' in again and f'data-version="{v1}"' in again


def test_the_server_logs_no_queries_in_either_mode(demo_ctx, tmp_path, make_search_corpus, caplog) -> None:
    """Owner's rule (SPEC §8): a query is never logged on the server, in either mode: not by the handlers, the
    search, the routes (`/detail` carries the query), uvicorn's access log or Caddy. Recent searches stay in the
    browser."""
    caplog.set_level(logging.DEBUG)  # every logger, every level
    secret = "quokka zebra 7731 diffusion"
    client = TestClient(make_app(build_blocks(demo_ctx), demo_ctx.resolve, demo=True))
    hit = demo_ctx.library.search("denoising diffusion").results[0]
    layout.run_search(demo_ctx, None, secret)
    client.get(f"/detail/{hit.file_id}?chunk={hit.chunk_id}&q=" + secret.replace(" ", "+"))
    _, sid = upload(demo_ctx, {"notes.txt": "Robotics club notes."})
    layout.run_search(demo_ctx, sid, secret, "My uploads")
    local = Library(Config(data_dir=tmp_path / "local"))
    local.index([make_search_corpus(tmp_path / "files")])
    layout.run_search(UiContext("local", local), None, secret)
    # "httpx" is the test's own client logging its requests (the app sets it to WARNING); everything else is ours,
    # Gradio's, FastAPI's or uvicorn's
    logged = " ".join(r.getMessage() for r in caplog.records if r.name != "httpx")
    assert caplog.records and "quokka" not in logged and "7731" not in logged

    repo = Path(__file__).resolve().parents[1]
    assert re.search(r"uvicorn\.run\([^)]*access_log=False", (repo / "app.py").read_text(encoding="utf-8"))
    assert not re.search(r"^\s*log\b", (repo / "deploy" / "Caddyfile").read_text(encoding="utf-8"), re.M)


def test_upload_refuses_paths_outside_gradios_folder(demo_ctx, tmp_path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    *_, (status, sid) = layout.handle_upload(demo_ctx, None, [str(secret)])
    assert sid is None and "not an uploaded file" in status


def test_open_file_is_local_only(tmp_path, fake_embedder, make_search_corpus, monkeypatch) -> None:
    opened = []
    monkeypatch.setattr(layout.platform, "open_file", lambda p: opened.append(p))
    lib = Library(Config(data_dir=tmp_path / "data"))
    lib.index([make_search_corpus(tmp_path / "files")])
    hit = lib.search("sourdough").results[0]
    assert layout.open_file(UiContext("local", lib), hit.file_id) == "Opened recipe.txt"
    assert layout.open_file(UiContext("demo", lib), hit.file_id) == "Nothing to open." and len(opened) == 1
    assert layout.open_file(UiContext("local", lib), "../../etc") == "Nothing to open."


def test_folders_panel_handlers(tmp_path, fake_embedder, make_search_corpus) -> None:
    ctx = UiContext("local", Library(Config(data_dir=tmp_path / "data")))
    root = make_search_corpus(tmp_path / "files")
    assert "Not a folder" in layout.index_folder(ctx, str(tmp_path / "nope"))
    assert "Add a folder first" in layout.index_folder(ctx, None)
    assert "6 added" in layout.index_folder(ctx, str(root))
    stats, roots = layout.folders_state(ctx)
    assert views._e(str(root.resolve())) in stats and roots["choices"] == [str(root.resolve())]
    assert "files · indexed" in layout.status_html(ctx, None)
    assert "6 files left" in layout.remove_folder(ctx, str(root.resolve()))


def test_opening_or_searching_the_page_never_creates_a_session(demo_ctx) -> None:
    """Owner's rule (19 Oct): only the first accepted upload creates a session, so crawlers can't use up the slots."""
    client = TestClient(make_app(build_blocks(demo_ctx), demo_ctx.resolve, demo=True))
    assert client.get("/?q=diffusion").status_code == 200
    layout.home_html(demo_ctx, None)
    layout.status_html(demo_ctx, None)
    layout.run_search(demo_ctx, None, "denoising diffusion")
    layout.run_search(demo_ctx, None, "denoising diffusion", "My uploads")
    for route in ("thumb", "preview", "detail", "file"):
        client.get(f"/{route}/1?s={uuid.uuid4()}")
    *_, (_, sid) = layout.handle_upload(demo_ctx, None, [gradio_file(demo_ctx, "notes.zip", "not supported")])
    assert sid is None and demo_ctx.sessions.sessions == {}


def test_with_all_sessions_open_uploads_say_busy_and_search_still_works(demo_ctx) -> None:
    """Owner's check (SPEC §8): with max_sessions (5) open, a new visitor's Add files and drop zone say the demo is
    busy; searching the sample files works for everyone, and a visitor who has a session can still add to it."""
    cap = demo_ctx.sessions.cfg.max_sessions
    assert cap == 5
    _, first = upload(demo_ctx, {"first.txt": "Notes from the first visitor about robotics."})
    for _ in range(cap - 1):
        demo_ctx.sessions._create()
    assert demo_ctx.sessions.full and len(demo_ctx.sessions.sessions) == cap

    home = layout.home_html(demo_ctx, None)
    assert views._e(views.DEMO_BUSY) in home and 'data-act="add"' not in home  # neither Add files nor the drop zone
    assert 'data-uploads="full"' in layout.status_html(demo_ctx, None)
    path = gradio_file(demo_ctx, "late.txt", "A latecomer's file.")
    *_, (status, sid) = layout.handle_upload(demo_ctx, None, [path])
    assert sid is None and views._e(views.DEMO_BUSY) in status and "late.txt" not in status
    assert not Path(path).exists()  # nothing kept, Gradio's copy included
    assert "diffusion.pdf" in layout.run_search(demo_ctx, None, "denoising diffusion")

    assert 'data-uploads="open"' in layout.status_html(demo_ctx, first)  # the first visitor can still add files
    status, again = upload(demo_ctx, {"second.txt": "More robotics notes."}, sid=first)
    assert again == first and 'data-summary="1 added"' in status
    assert "second.txt" in layout.run_search(demo_ctx, first, "robotics notes", "My uploads")
