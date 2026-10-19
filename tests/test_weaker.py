"""The floors and the gate never cut the list short (SPEC §7.4 step 4), the Visual matches row (open item t),
and the upload scope filter."""

from pathlib import Path

from PIL import Image

from conftest import COLOURS, camera_exif, save_image
from vault.config import Config
from vault.ingest.pipeline import index_paths
from vault.search.engine import search


def photo(root: Path, name: str, colour: str, shade: int = 0) -> Path:
    rgb = tuple(min(255, c + shade) for c in COLOURS[colour])
    return save_image(Image.new("RGB", (640, 480), rgb), root / name, exif=camera_exif())


def names(results) -> list[str]:
    return [r.filename for r in results]


def small_library(root: Path) -> Path:
    photo(root, "IMG_red.jpg", "red")
    for i, colour in enumerate(["green", "blue", "gray", "yellow"]):
        photo(root, f"IMG_{i}.jpg", colour)
    (root / "bills.txt").write_text("The electricity invoice is due on the fifth.", encoding="utf-8")
    (root / "recipe.txt").write_text("Sourdough: mix flour, water and starter.", encoding="utf-8")
    return root


def test_a_visual_query_keeps_one_strong_match_and_lists_the_rest_below(conn, cfg, fake_embedder, tmp_path) -> None:
    index_paths(conn, [small_library(tmp_path / "c")], cfg)
    resp = search(conn, "red", cfg)
    assert names(resp.results) == ["IMG_red.jpg"]  # fusion: the gate kept one image, the floor no text
    weaker = names(resp.weaker)
    assert len(weaker) == 6 and set(weaker) == {"IMG_0.jpg", "IMG_1.jpg", "IMG_2.jpg", "IMG_3.jpg", "bills.txt",
                                                "recipe.txt"}
    assert all(r.weaker and "weaker match" in r.badges for r in resp.weaker)
    assert not any(r.weaker for r in resp.results)


def test_weaker_matches_never_repeat_a_result_and_respect_the_cap(conn, cfg, fake_embedder, tmp_path) -> None:
    root = small_library(tmp_path / "c")
    for i in range(30):
        photo(root, f"IMG_gray_{i:02}.jpg", "gray", shade=i + 1)
    index_paths(conn, [root], cfg)
    resp = search(conn, "red", cfg, limit=12)
    ids = [r.file_id for r in resp.results + resp.weaker]
    assert len(ids) == 12 and len(set(ids)) == 12
    assert search(conn, "red", cfg).weaker  # the default cap leaves room for them


def test_weaker_matches_keep_the_kind_filter(conn, cfg, fake_embedder, tmp_path) -> None:
    index_paths(conn, [small_library(tmp_path / "c")], cfg)
    resp = search(conn, "red", cfg, kinds={"doc"})
    assert resp.results == [] and set(names(resp.weaker)) == {"bills.txt", "recipe.txt"}
    only_one = search(conn, "sourdough", cfg, kinds={"doc"})
    assert names(only_one.results) == ["recipe.txt"] and names(only_one.weaker) == ["bills.txt"]


def test_weaker_matches_can_be_switched_off(conn, cfg, fake_embedder, tmp_path) -> None:
    index_paths(conn, [small_library(tmp_path / "c")], cfg)
    off = Config(data_dir=cfg.data_dir, weaker_matches=False)
    assert search(conn, "red", off).weaker == []


def test_visual_row_holds_the_gated_clip_hits(conn, cfg, fake_embedder, tmp_path) -> None:
    root = small_library(tmp_path / "c")
    photo(root, "IMG_red2.jpg", "red", shade=3)
    index_paths(conn, [root], cfg)
    resp = search(conn, "red", cfg)
    assert set(names(resp.visual)) == {"IMG_red.jpg", "IMG_red2.jpg"}
    assert all(r.thumb_url for r in resp.visual)
    assert search(conn, "electricity invoice", cfg).visual == []  # the gate kept no image


def test_filter_only_search_has_no_tail_or_row(conn, cfg, fake_embedder, tmp_path) -> None:
    index_paths(conn, [small_library(tmp_path / "c")], cfg)
    resp = search(conn, "photos", cfg)
    assert len(resp.results) == 5 and resp.weaker == [] and resp.visual == []


def test_roots_scope_every_list(conn, cfg, fake_embedder, tmp_path) -> None:
    samples, uploads = tmp_path / "samples", tmp_path / "uploads"
    photo(samples, "IMG_sample_red.jpg", "red")
    (samples / "bills.txt").write_text("The electricity invoice is due on the fifth.", encoding="utf-8")
    photo(uploads, "IMG_upload_red.jpg", "red", shade=5)
    (uploads / "invoice.txt").write_text("Electricity invoice, October.", encoding="utf-8")
    index_paths(conn, [samples, uploads], cfg)
    up = str(uploads.resolve())
    mine = search(conn, "red", cfg, roots=[up])
    assert names(mine.results + mine.weaker) == ["IMG_upload_red.jpg", "invoice.txt"]
    assert names(mine.visual) == ["IMG_upload_red.jpg"]
    assert names(search(conn, "electricity invoice", cfg, roots=[up]).results) == ["invoice.txt"]
    assert set(names(search(conn, "", cfg, roots=[up]).results)) == {"IMG_upload_red.jpg", "invoice.txt"}
    both = search(conn, "electricity invoice", cfg)
    assert set(names(both.results)) == {"bills.txt", "invoice.txt"}


def test_found_by_ranks_are_real_for_related_matches_and_shares_add_up(conn, cfg, fake_embedder, tmp_path) -> None:
    """SPEC §8 Found by: every result shows each method's real rank; related matches too (in the full list)."""
    root = small_library(tmp_path / "c")
    for i in range(3):
        photo(root, f"IMG_extra_{i}.jpg", "gray", shade=i + 1)
    index_paths(conn, [root], cfg)
    resp = search(conn, "red", cfg)
    for r in resp.results + resp.weaker:
        assert abs(sum(r.shares.values()) - r.rrf_score) < 1e-12
    images = [r for r in resp.weaker if r.kind == "photo"]
    clip_ranks = sorted(r.ranks["clip"] for r in images)
    assert clip_ranks == list(range(2, 2 + len(images)))  # rank 1 is the red photo, in the main list
    assert all(r.clip_status == "not kept" for r in images)
    assert resp.results[0].clip_status == "strong" and resp.results[0].ranks["clip"] == 1
    texts = [r for r in resp.weaker if r.kind == "doc"]
    assert texts and all(r.ranks["dense"] >= 1 and r.chunk_source == "doc_text" for r in texts)


def test_results_carry_the_preview_facts(conn, cfg, fake_embedder, tmp_path) -> None:
    root = small_library(tmp_path / "c")
    index_paths(conn, [root], cfg)
    r = search(conn, "sourdough", cfg).results[0]
    assert r.filename == "recipe.txt" and r.size_bytes == (root / "recipe.txt").stat().st_size
    assert r.chunk_source == "doc_text" and r.text_sources == ["doc_text"]
    p = search(conn, "red", cfg).results[0]
    assert (p.width, p.height) == (640, 480) and p.text_sources == []


def checklists(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "lab.txt").write_text("Lab checklist: switch on the fume hood before the session.", encoding="utf-8")
    (root / "shopping.txt").write_text("Shopping checklist: bread, milk, eggs, coffee, rice, tea, butter.",
                                       encoding="utf-8")
    (root / "recipe.txt").write_text("Sourdough: mix flour, water and starter.", encoding="utf-8")
    return root


def test_with_no_bar_every_fused_result_is_above_the_divider(conn, cfg, fake_embedder, tmp_path) -> None:
    from dataclasses import replace

    index_paths(conn, [checklists(tmp_path / "c")], cfg)
    resp = search(conn, "fume hood checklist", replace(cfg, divider_min_share=0.0))
    assert names(resp.results) == ["lab.txt", "shopping.txt"]


def test_a_keyword_match_on_one_word_of_several_goes_below_the_divider(conn, cfg, fake_embedder, tmp_path) -> None:
    """Divider candidate "terms" (M6 item 3): a file found by keyword search alone, holding one of three query
    words, is a weaker match; a one-word query keeps its keyword matches above."""
    from dataclasses import replace

    index_paths(conn, [checklists(tmp_path / "c")], cfg)
    bar = replace(cfg, divider_min_share=0.0, divider_min_terms=2)
    resp = search(conn, "fume hood checklist", bar)
    assert names(resp.results) == ["lab.txt"] and names(resp.weaker)[0] == "shopping.txt"
    moved = resp.weaker[0]
    assert moved.weaker and "weaker match" in moved.badges and moved.ranks["bm25"] == 2
    assert moved.below == "found by keyword alone, holding 1 of the 3 query words"
    assert set(names(search(conn, "checklist", bar).results)) == {"lab.txt", "shopping.txt"}


def test_a_result_far_below_the_top_score_goes_below_the_divider(conn, cfg, fake_embedder, tmp_path) -> None:
    """Divider candidate "share": a cut at a share of the top result's RRF score; the order doesn't change."""
    from dataclasses import replace

    index_paths(conn, [checklists(tmp_path / "c")], cfg)
    resp = search(conn, "fume hood checklist", replace(cfg, divider_min_share=0.5))
    assert names(resp.results) == ["lab.txt"] and names(resp.weaker)[0] == "shopping.txt"
    assert resp.weaker[0].below == "scored under 50% of the top result"
    assert names(search(conn, "fume hood checklist", replace(cfg, divider_min_share=0.3)).results) == [
        "lab.txt", "shopping.txt"]  # 1/62 is 33% of 3/61


def test_divide_keeps_order_and_says_why() -> None:
    from vault.search.engine import divide
    from vault.search.fuse import Fused

    fused = [Fused(1, 0.05, {"bm25": 1, "dense": 1}), Fused(2, 0.03, {"bm25": 2}), Fused(3, 0.02, {"dense": 2}),
             Fused(4, 0.01, {"bm25": 3, "all_terms": 1})]
    assert divide(fused, Config(divider_min_share=0.0)) == (fused, {})
    above, why = divide(fused, Config(divider_min_share=0.0, divider_min_terms=3), {2: 1, 4: 2}, n_terms=2)
    assert [f.file_id for f in above] == [1, 3, 4] and set(why) == {2}  # needs 2 of 2: file 4 holds both
    above, why = divide(fused, Config(divider_min_share=0.5))
    assert [f.file_id for f in above] == [1, 2] and set(why) == {3, 4}
