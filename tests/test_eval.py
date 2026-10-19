"""`vault eval` (SPEC §9): query import and split, keys for public and private files, metrics, a run end to end,
and that committed output never names a private file. Generated fixtures only."""

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from vault import eval as ev
from vault.__main__ import main
from vault.config import Config
from vault.search.gate import gate
from vault.search.parse import parse


def make_paths(tmp_path: Path) -> ev.Paths:
    repo = tmp_path / "repo"
    return ev.Paths(repo=repo, corpus=repo / "corpus", private=repo / "data" / "eval_private",
                    index=repo / "data" / "eval_index", out=repo / "eval")


def write_csv(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["query", "expected_file", "search_type"])
        w.writerows(rows)
    return path


def setup_files(paths: ev.Paths) -> None:
    (paths.corpus / "real").mkdir(parents=True)
    (paths.corpus / "real" / "garden plan.md").write_text("Tomatoes along the south fence.", encoding="utf-8")
    (paths.corpus / "real" / "dice.png").write_bytes(b"png")
    paths.private_files.mkdir(parents=True)
    (paths.private_files / "secret notes.txt").write_text("Quokka budget meeting on Friday.", encoding="utf-8")
    with paths.ids_file.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([["id", "name"], ["private-01", "secret notes.txt"]])


def test_import_maps_public_paths_and_private_ids_and_drops_left_out_files(tmp_path) -> None:
    paths = make_paths(tmp_path)
    setup_files(paths)
    src = write_csv(tmp_path / "queries.csv", [
        ("where are the tomatoes", "garden plan.md", "meaning"),
        ("quokka budget", "secret notes.txt", "exact word"),
        ("both", "garden plan.md|secret notes.txt", "meaning"),
        ("a removed photo", "left out.jpg", "picture"),
        ("dice, or a removed photo", "dice.png|left out.jpg", "picture"),
    ])
    before = src.read_bytes()
    rep = ev.import_queries([src], paths)
    assert src.read_bytes() == before  # the owner's file is never changed
    by_id = {q.id: q for q in rep.queries}
    assert by_id["q01"].relevant == ["corpus/real/garden plan.md"] and by_id["q01"].public
    assert by_id["q02"].relevant == ["private-01"] and not by_id["q02"].public
    assert by_id["q03"].relevant == ["corpus/real/garden plan.md", "private-01"] and not by_id["q03"].public
    assert rep.left_out == [("q04", "every correct file was left out of the corpus")] and "q04" not in by_id
    assert rep.trimmed == [("q05", 1)] and by_id["q05"].relevant == ["corpus/real/dice.png"]

    ev.write_queries(rep.queries, paths)
    committed = paths.public_queries.read_text(encoding="utf-8") + paths.split_file.read_text(encoding="utf-8")
    assert "secret" not in committed and "quokka" not in committed.lower() and "private-01" not in committed
    assert sorted(json.loads(paths.split_file.read_text(encoding="utf-8"))["splits"]) == ["q01", "q02", "q03", "q05"]
    assert "secret notes" not in paths.full_queries.read_text(encoding="utf-8")  # ids, even in the private file
    assert [q.id for q in ev.load_queries(paths)] == ["q01", "q02", "q03", "q05"]


def test_the_split_is_stratified_fixed_and_stable_when_queries_are_added(tmp_path) -> None:
    paths = make_paths(tmp_path)
    setup_files(paths)
    rows = [(f"query {i}", "garden plan.md", cat) for cat, n in (("meaning", 19), ("picture", 10),
                                                                  ("exact word", 9), ("inside a scan", 6))
            for i in range(n)]
    owner = write_csv(tmp_path / "queries.csv", rows)
    first = {q.id: q.split for q in ev.import_queries([owner], paths).queries}
    again = {q.id: q.split for q in ev.import_queries([owner], paths).queries}
    assert first == again  # a fixed seed
    qs = ev.import_queries([owner], paths).queries
    for cat in ("meaning", "picture", "exact word", "inside a scan"):
        splits = [q.split for q in qs if q.category == cat]
        assert abs(splits.count("tune") - splits.count("heldout")) <= 1
    assert abs(list(first.values()).count("tune") - list(first.values()).count("heldout")) <= 1
    extra = write_csv(tmp_path / "queries_extra.csv", [("extra one", "dice.png", "picture"),
                                                       ("extra two", "dice.png", "picture")])
    both = {q.id: q.split for q in ev.import_queries([owner, extra], paths).queries}
    assert {k: v for k, v in both.items() if k.startswith("q")} == first  # added queries never move earlier ones
    assert sorted(v for k, v in both.items() if k.startswith("x")) == ["heldout", "tune"]


def test_metrics_count_hits_and_mrr() -> None:
    outs = [ev.Outcome(r, 10, True, {}, 1.0) for r in (1, 3, 7, None, 12)]
    m = ev.metrics(outs)
    assert (m["n"], m["r1"], m["r5"], m["r10"]) == (5, 1, 2, 3) and m["r10_pct"] == 60.0
    assert m["mrr"] == round((1 + 1 / 3 + 1 / 7) / 5, 3)
    assert ev.percentile([5, 1, 3, 2, 4], 0.5) == 3


def test_settings_parse_to_typed_config_values() -> None:
    over = ev.parse_settings(["dense_min_cosine=0.5", "weights.clip=0", "clip_gated=false", "rrf_k=40"])
    cfg = ev.configure(Config(), over)
    assert cfg.dense_min_cosine == 0.5 and cfg.weights["clip"] == 0 and cfg.weights["dense"] == 1.0
    assert cfg.clip_gated is False and cfg.rrf_k == 40
    with pytest.raises(ValueError):
        ev.parse_settings(["no_such_setting=1"])


def test_a_run_end_to_end_names_private_files_by_id_only(tmp_path, fake_embedder, make_search_corpus) -> None:
    paths = make_paths(tmp_path)
    make_search_corpus(paths.corpus)
    paths.private_files.mkdir(parents=True)
    (paths.private_files / "secret notes.txt").write_text("Quokka budget meeting on Friday.", encoding="utf-8")
    with paths.ids_file.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([["id", "name"], ["private-01", "secret notes.txt"]])
    queries = [ev.Query("q01", "singular value decomposition", "exact word",
                        ["corpus/papers/linear algebra notes.pdf"], "tune"),
               ev.Query("q02", "quokka budget", "exact word", ["private-01"], "tune"),
               ev.Query("q03", "zzzz nothing matches this", "meaning", ["corpus/home/recipe.txt"], "heldout")]
    lib = ev.eval_library(Config(), paths)
    run = ev.evaluate(lib, queries, rows=list(ev.ABLATIONS), paths=paths)
    app = run.rows[ev.APP]
    assert app["q01"].rank == 1 and app["q02"].rank == 1 and app["q01"].methods.get("bm25") == 1
    assert run.rows["3. CLIP only"]["q01"].rank is None  # no images: CLIP finds nothing
    assert run.types["corpus/papers/linear algebra notes.pdf"] == "pdf" and run.types["private-01"] == "text"
    md, data = ev.report(run, splits=["tune", "heldout"], detail=["tune"], title="t")
    text = md + json.dumps(data)
    assert "secret notes" not in text and "Quokka" not in text and "quokka" not in text
    assert "private-01" in text and "singular value decomposition" in text
    assert "queries" in data["splits"]["tune"] and "queries" not in data["splits"]["heldout"]  # no held-out detail
    s = data["splits"]["tune"]
    assert s["rows"][ev.APP]["overall"]["r1"] == 2 and s["rows"][ev.APP]["set"]["private"]["n"] == 1
    assert s["divider"]["of"] == 2 and s["divider"]["all_correct_above"] == 2
    assert s["divider"]["set"]["private"] == {"median_above": s["divider"]["set"]["private"]["median_above"],
                                              "all_correct_above": 1, "of": 1}
    lines, cmp = ev.compare(data, data)  # a final run against its baseline: each half, overall and by set
    table = "\n".join(lines)
    assert "| Tuning half | all | 2 | 2 → 2 (100% → 100%) |" in table and "| Tuning half | private | 1 |" in table
    assert cmp["tune"]["final"]["divider"]["all_correct_above"] == 2 and "secret" not in table


def test_the_ungated_ablation_keeps_every_clip_candidate() -> None:
    ids, scores = np.arange(30), np.linspace(0.1, 0.3, 30)
    gated = gate(ids, scores, parse("a red car"), Config())
    ungated = gate(ids, scores, parse("a red car"), Config(clip_gated=False))
    assert len(ungated.hits) == 30 and ungated.hits[0][0] == 29 and not ungated.strong
    assert len(gated.hits) < 30


def test_a_held_out_run_needs_a_purpose() -> None:
    """Owner's rule (§9): the held-out half runs for the baseline and once for the final settings, never casually."""
    assert main(["eval", "--split", "heldout"]) == 2
    assert main(["eval", "--split", "all"]) == 2


def test_the_generated_set_is_imported_apart_and_never_split(tmp_path) -> None:
    """Owner's rule (19 Oct): the generated-file set is reported apart and is a guardrail in tuning, never tuned
    toward, so it has no tuning or held-out half."""
    paths = make_paths(tmp_path)
    setup_files(paths)
    src = write_csv(tmp_path / "queries_generated.csv", [("tomatoes", "garden plan.md", "meaning"),
                                                         ("dice", "dice.png", "picture")])
    rep = ev.import_queries([src], paths, sources=("generated",))
    assert [(q.id, q.split, q.source) for q in rep.queries] == [("g01", ev.GUARD, "generated"),
                                                                 ("g02", ev.GUARD, "generated")]
    ev.write_generated(rep.queries, paths)
    assert [q.id for q in ev.load_queries(paths, "generated")] == ["g01", "g02"]
