"""The Cortex Vault UI (SPEC §8): server-built HTML, grouping, the id-only routes, demo safety, and the handlers,
without a browser and without real models."""

import re
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
        views.upload_outcome([FileStatus(EVIL, "indexed"), FileStatus(EVIL, "rejected", EVIL)], received=2),
        views.home(DEMO, count=1, recent=[Recent(1, EVIL, "photo", "2026-10-18T10:00:00")], suggestions=[]),
        views.empty(response([]), DEMO, query=EVIL),
    ]
    for page in pages:
        assert "<img src=x" not in page and "onerror=alert(1)>" not in page
        assert "&lt;img src=x onerror=alert(1)&gt;" in page


def test_snippets_keep_only_mark() -> None:
    r = result(1, snippet_html="&lt;script&gt; <mark>denied</mark>")
    page = views.results(response([r]), DEMO, place, query="denied")
    assert "<script" not in page and "<mark>denied</mark>" in page


# ---------------------------------------------------------------- grouping and counts


def groups(page: str) -> list[str]:
    return re.findall(r'data-group="(\w+)"', page)


def test_groups_follow_the_best_fused_rank_and_related_come_last() -> None:
    """Owner's rule: Close and Visual matches in the order of their best item's fused rank; Related last."""
    text_first = response([result(1), visual(2), result(3)], weaker=[result(4, weaker=True)])
    assert groups(views.results(text_first, DEMO, place, query="q")) == ["close", "visual", "related"]


def test_a_query_whose_top_result_only_clip_found_shows_visual_matches_first() -> None:
    picture_first = response([visual(1), result(2), visual(3)], weaker=[result(4, weaker=True)])
    page = views.results(picture_first, DEMO, place, query="sunset")
    assert groups(page) == ["visual", "close", "related"]
    assert re.findall(r'role="option" id="cv-r-(\d+)"', page) == ["1", "3", "2", "4"]  # the arrow keys' order


def test_show_n_more_counts_what_is_left_after_twenty() -> None:
    page = views.results(response([result(i) for i in range(1, 31)],
                                  weaker=[result(i, weaker=True) for i in range(31, 51)]), DEMO, place, query="q")
    assert "Show 30 more" in page and page.count("data-more hidden") == 30
    assert "Show" not in views.results(response([result(i) for i in range(1, 6)]), DEMO, place, query="q")


def test_the_top_twenty_by_rank_show_even_if_their_group_comes_later() -> None:
    """A visual match at #2 after a long Close group stays visible; Show more holds the lowest ranked."""
    page = views.results(response([result(1), visual(2), *[result(i) for i in range(3, 31)]]), DEMO, place, query="q")
    assert groups(page) == ["close", "visual"]
    thumb = re.search(r'<div class="cv-thumb" [^>]*>', page).group(0)
    assert "hidden" not in thumb and 'data-rank="1"' in thumb
    assert "Show 10 more" in page and page.count("data-more hidden") == 10


def test_chip_counts_come_from_the_full_result_set() -> None:
    rs = [result(1), result(2, kind="pdf"), visual(3), result(4, kind="doc", filename="a.docx")]
    page = views.results(response(rs, weaker=[result(5, kind="slides", weaker=True)]), DEMO, place, query="q")
    counts = dict(re.findall(r'data-filter="(\w+)"[^>]*>[^<]*<span class="cv-kind__n">(\d+)', page))
    assert counts == {"all": "5", "screenshot": "1", "photo": "1", "pdf": "1", "slides": "1", "doc": "1"}
    assert 'data-meta="5 results · 0.21 s"' in page


# ---------------------------------------------------------------- preview facts


def test_found_by_names_each_method_with_its_real_rank() -> None:
    assert views.found_by(result(1, ranks={"bm25": 1, "all_terms": 1, "dense": 2, "clip": None})) == \
        "Exact words (rank 1), every word (rank 1) and meaning (rank 2)"
    assert views.found_by(result(1, ranks={"dense": 37}, weaker=True)) == "Meaning (rank 37)"
    assert views.found_by(result(1, ranks={})) == ""


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


# ---------------------------------------------------------------- nothing served loads from the internet


def test_no_served_html_css_or_js_contains_an_external_url() -> None:
    served = [p for p in STATIC.rglob("*") if p.suffix in (".css", ".js", ".svg", ".html")]
    assert {p.suffix for p in served} >= {".css", ".js", ".svg"}
    texts = [p.read_text(encoding="utf-8") for p in served]
    texts += [HEAD, views.top(DEMO), views.top(LOCAL), views.footer(DEMO), views.sprite()]
    texts.append(views.results(response([result(1), visual(2)], weaker=[result(3, weaker=True)]), DEMO, place,
                               query="q"))
    texts.append(views.home(DEMO, count=1, recent=[], suggestions=views.DEMO_SUGGESTIONS, samples=52))
    for text in texts:
        urls = re.findall(r"https?://[^\s\"')<>]+", text)
        assert [u for u in urls if u != "http://www.w3.org/2000/svg"] == []


def test_the_theme_names_plain_fonts() -> None:
    from vault.ui.server import theme

    fonts = str(theme().font)
    assert "Geist" in fonts and "GoogleFont" not in fonts


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
    *_, (main, status, sid) = layout.handle_upload(ctx, sid, [gradio_file(ctx, n, t) for n, t in files.items()])
    return main, status, sid


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
    _, _, a = upload(demo_ctx, {"one.txt": "Robotics club budget.", "two.txt": "Robotics club minutes, October."})
    _, _, b = upload(demo_ctx, {"other.txt": "A recipe for flatbread."})
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
    assert 'data-view="home"' in home and "Search 2 files" in home and "bayes theorem" in home
    page = layout.run_search(demo_ctx, None, "  denoising   diffusion ")
    assert 'data-query="denoising diffusion"' in page and "diffusion.pdf" in page
    nonsense = layout.run_search(demo_ctx, None, "zzzqqq xylophone")
    assert "No close matches for “zzzqqq xylophone”" in nonsense and "Related matches" in nonsense
    assert "Nothing uploaded yet" in layout.run_search(demo_ctx, None, "diffusion", "My uploads")


def test_upload_outcome_then_scopes(demo_ctx) -> None:
    main, status, sid = upload(demo_ctx, {"minutes.txt": "Minutes: the diffusion reading group meets Friday."})
    assert sid and "1 file received · 1 added" in main and "minutes.txt" in main
    assert views._e(views.UPLOAD_NOTICE) in main and 'data-text="3 files · up to date"' in status
    mine = layout.run_search(demo_ctx, sid, "diffusion", "My uploads")
    samples = layout.run_search(demo_ctx, sid, "diffusion", "Samples")
    assert "minutes.txt" in mine and "diffusion.pdf" not in mine.split("Related matches")[0]
    assert "diffusion.pdf" in samples and "minutes.txt" not in samples and f"s={sid}" in mine


def test_upload_outcome_shows_while_the_files_are_still_read(demo_ctx, monkeypatch) -> None:
    """Two-phase indexing (SPEC §6.1): the per-file outcome appears once the files are listed; the header then
    reports "Reading X of Y uploads" until phase 2 is done."""
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
    first_main = next(i for i, (main, _, _) in enumerate(steps) if isinstance(main, str))
    assert "3 files received · 3 added" in steps[first_main][0] and first_main < len(steps) - 1
    busy = [status for _, status, _ in steps[first_main:-1]]
    assert any("Reading 2 of 3 uploads" in s for s in busy) and all('data-busy="true"' in s for s in busy[1:])
    assert any("still being read" in s for s in busy)
    assert 'data-busy="false"' in steps[-1][1]


def test_upload_refuses_paths_outside_gradios_folder(demo_ctx, tmp_path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    *_, (main, _, sid) = layout.handle_upload(demo_ctx, None, [str(secret)])
    assert sid is None and "not an uploaded file" in main


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


def test_with_twenty_sessions_open_uploads_say_busy_and_search_still_works(demo_ctx) -> None:
    """Owner's check (SPEC §8): max_sessions = 20 open, a new visitor's Add files and drop zone say the demo is busy;
    searching the sample files works for everyone, and a visitor who has a session can still add to it."""
    assert demo_ctx.sessions.cfg.max_sessions == 20
    _, _, first = upload(demo_ctx, {"first.txt": "Notes from the first visitor about robotics."})
    for _ in range(19):
        demo_ctx.sessions._create()
    assert demo_ctx.sessions.full and len(demo_ctx.sessions.sessions) == 20

    home = layout.home_html(demo_ctx, None)
    assert views._e(views.DEMO_BUSY) in home and 'data-act="add"' not in home.split("cv-home")[1]
    assert 'data-uploads="full"' in layout.status_html(demo_ctx, None)
    path = gradio_file(demo_ctx, "late.txt", "A latecomer's file.")
    *_, (main, status, sid) = layout.handle_upload(demo_ctx, None, [path])
    assert sid is None and views._e(views.DEMO_BUSY) in main and "late.txt" not in main
    assert not Path(path).exists()  # nothing kept, Gradio's copy included
    assert "diffusion.pdf" in layout.run_search(demo_ctx, None, "denoising diffusion")

    assert 'data-uploads="open"' in layout.status_html(demo_ctx, first)  # the first visitor can still add files
    main, _, again = upload(demo_ctx, {"second.txt": "More robotics notes."}, sid=first)
    assert again == first and "1 added" in main
    assert "second.txt" in layout.run_search(demo_ctx, first, "robotics notes", "My uploads")
