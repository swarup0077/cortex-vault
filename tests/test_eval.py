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
    _, both = ev.report(run, splits=["tune", "heldout"], detail=["tune", "heldout"], title="final")
    md, rerun = ev.divider_rerun(run, both, ["tune", "heldout"])  # same settings: the ranking must match
    assert rerun["identical"] and "queries" not in json.dumps(rerun) and "| Held-out half | 1 | yes | yes |" in md
    both["divider_changed_after_final"] = {"splits": {"tune": {"divider": {"median_above": 1, "all_correct_above": 0,
                                                                            "of": 2}}}}
    current = ev.with_current_divider(both)  # later runs compare with the divider's numbers at the current bar
    assert current["splits"]["tune"]["divider"]["median_above"] == 1 and both["splits"]["tune"]["divider"]["of"] == 2


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


def _half(r1: int) -> dict:
    return {"rows": {ev.APP: {"overall": {"n": 2, "r1": r1, "r5": 2, "r10": 2, "mrr": 0.75},
                              "search type": {"picture": {"n": 1, "r1": 0, "r5": 1, "r10": 1}}}},
            "divider": {"median_above": 3, "all_correct_above": 2, "of": 2}}


def test_every_held_out_run_goes_side_by_side_in_the_current_report(tmp_path, monkeypatch) -> None:
    """Owner, 19 Oct: every logged run on the held-out half side by side in eval/results.md. An early log line is
    matched to its report by time and commit, a newer one names its report, and a divider rerun shows the
    divider only."""
    paths = make_paths(tmp_path)
    paths.out.mkdir(parents=True)
    final = {"when": "2026-10-19T17:04:07", "commit": "aaa1111", "corpus": {"files": 95},
             "splits": {s: _half(1) for s in ("tune", "heldout", "all")}}
    rerun = {"when": "2026-10-19T17:39:30", "commit": "bbb2222", "final_run": {"when": final["when"],
                                                                              "commit": final["commit"]},
             "splits": {s: {"divider": {"median_above": 1.5, "all_correct_above": 1, "of": 2}}
                        for s in ("tune", "heldout", "all")}}
    for name, d in (("results_final", final), ("results_divider", rerun)):
        (paths.out / f"{name}.json").write_text(json.dumps(d), encoding="utf-8")
    paths.heldout_log.write_text("".join(json.dumps({"when": d["when"], "purpose": p, "commit": d["commit"],
                                                     "settings": {}}) + "\n"
                                         for p, d in (("final", final), ("divider-rerun", rerun))), encoding="utf-8")
    current = {**final, "when": "2026-10-20T09:00:00", "commit": "ccc3333",
               "splits": {s: _half(2) for s in ("tune", "heldout", "all")}}
    (paths.out / "results.json").write_text(json.dumps(current), encoding="utf-8")
    (paths.out / "results.md").write_text("# Eval\n\nRun at ccc3333.\n\n## Tuning half (2 queries)\n\nrows\n",
                                          encoding="utf-8")
    monkeypatch.setattr(ev, "_commit", lambda: "ccc3333")  # the log records the commit the run was made at
    assert ev.log_heldout(paths, "post-z", {}, when=current["when"], report=paths.out / "results",
                          note="the gap") == 1
    assert json.loads(paths.heldout_log.read_text(encoding="utf-8").splitlines()[-1])["report"] == "eval/results.json"
    ev.put_heldout_runs(paths)
    ev.put_heldout_runs(paths)  # a later run replaces the section
    md = (paths.out / "results.md").read_text(encoding="utf-8")
    assert md.count("## Every run on the held-out half") == 1
    assert md.index("## Every run on the held-out half") < md.index("## Tuning half")
    held = md.split("**Held-out half**")[1].split("**Both halves**")[0]
    assert "| 1 | final: after fixes x, y, n and f; the divider's first bar (0.31) | 10-19 17:04, aaa1111 | 95 | 1 |" in held
    assert "| 2 | divider-rerun: the divider's bar at 0.50" in held and "| 95 | as the final run |" in held
    assert "| 1.5 | 1 of 2 |" in held
    assert "| 3 | post-z: the gap | 10-20 09:00, ccc3333 | 95 | 2 | 2 | 2 | 0.750 | 0 / 1 / 1 of 1 | 3 | 2 of 2 |" in held


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
