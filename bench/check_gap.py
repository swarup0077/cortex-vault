"""Open item z: check `clip_strong_gap` on the eval's tuning half and the generated set, at both index sizes.

    python bench/check_gap.py [--gaps 0 0.035]

The gap was calibrated on generated scenes (`calibrate.py gap`); this checks it, it doesn't tune it. For each index
(the demo index: corpus/, the generated set only; the eval index: corpus/ plus the private files; the scale
index: the same plus the 1,000 generated files of `vault eval --purpose scale --extra ...`), each gap, and each query of the tuning half and the generated set: the
rank of the first correct file and whether the visual-query rule fired. Gap 0 is the z bar alone, the rule before
item z. Reported by search type, picture queries apart.

Only the tuning half and the generated set are read; a held-out query never reaches this script. Writes
bench/generated/check_gap.json; query ids only, never a private query's text.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

from vault import eval as ev  # noqa: E402
from vault.config import Config  # noqa: E402
from vault.search import engine  # noqa: E402
from vault.search.engine import search  # noqa: E402

OUT = REPO / "bench" / "generated"
# name → (index, query sets): the demo index has no private files, so only the generated set runs there
INDEXES = {"demo": (REPO / "demo_index", ("generated",)),
           "eval": (REPO / "data" / "eval_index", ("tune", "generated")),
           "scale": (REPO / "data" / "eval_scale_index", ("tune", "generated"))}

_top: dict = {}  # CLIP's top hit for the last search: its z over the candidates and its lead over the runner-up
_gate = engine.gate


def _spy(ids, scores, pq, cfg):
    if len(scores) >= 2 and scores.std() > 0:
        o = np.argsort(-scores, kind="stable")
        _top.update(z=round(float((scores[o[0]] - scores.mean()) / scores.std()), 2),
                    lead=round(float(scores[o[0]] - scores[o[1]]), 4))
    return _gate(ids, scores, pq, cfg)


engine.gate = _spy


def run_index(name: str, index: Path, which: tuple[str, ...], gaps: list[float]) -> dict:
    paths = replace(ev.Paths(), index=index)
    lib = ev.eval_library(Config(), paths, index=False)
    ids = ev.private_ids(paths)
    sets = {"tune": [q for q in ev.load_queries(paths, "owner") if q.split == "tune"],
            "generated": ev.load_queries(paths, "generated")}
    sets = {k: v for k, v in sets.items() if k in which}
    assert all(q.split == "tune" for q in sets.get("tune", []))  # never the held-out half
    out: dict = {}
    with lib.connect() as conn:
        text_index, image_index = lib.vectors(conn)
        files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        out["files"], out["images"] = files, len(image_index)
        for gap in gaps:
            cfg = replace(lib.cfg, clip_strong_gap=gap)
            for set_name, qs in sets.items():
                rows = []
                for q in qs:
                    _top.clear()
                    resp = search(conn, q.text, cfg, text_index=text_index, image_index=image_index)
                    o = ev.outcome(resp, q.relevant, lambda p: ev.key_of(p, paths, ids))
                    rows.append({"id": q.id, "type": q.category, "rank": o.rank, "fired": resp.clip["visual"],
                                 "top_z": _top.get("z"), "top_lead": _top.get("lead"), "_o": o})
                by_type = {}
                for t in sorted({r["type"] for r in rows}):
                    rs = [r for r in rows if r["type"] == t]
                    by_type[t] = {**ev.metrics([r["_o"] for r in rs]), "fired": sum(r["fired"] for r in rs)}
                out[f"{gap}/{set_name}"] = {
                    "all": {**ev.metrics([r["_o"] for r in rows]), "fired": sum(r["fired"] for r in rows)},
                    "by_type": by_type,
                    "queries": [{k: v for k, v in r.items() if k != "_o"} for r in rows]}
                m = out[f"{gap}/{set_name}"]["all"]
                print(f"{name} ({files} files, {len(image_index)} images) gap {gap} {set_name}: R@1/5/10 "
                      f"{m['r1']}/{m['r5']}/{m['r10']} of {m['n']}, MRR {m['mrr']:.3f}, rule fired {m['fired']}; "
                      + "; ".join(f"{t} {v['r1']}/{v['r5']}/{v['r10']} of {v['n']} fired {v['fired']}"
                                  for t, v in by_type.items()))
    return out


def changes(res: dict, a: float, b: float) -> dict:
    """Per query, the rank and the rule under gap a → gap b, where either changed."""
    out = {}
    for set_name in ("tune", "generated"):
        if f"{a}/{set_name}" not in res:
            continue
        qa = {q["id"]: q for q in res[f"{a}/{set_name}"]["queries"]}
        out[set_name] = [{"id": q["id"], "type": q["type"], "rank": f"{qa[q['id']]['rank']} → {q['rank']}",
                          "fired": f"{qa[q['id']]['fired']} → {q['fired']}"}
                         for q in res[f"{b}/{set_name}"]["queries"]
                         if (qa[q["id"]]["rank"], qa[q["id"]]["fired"]) != (q["rank"], q["fired"])]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gaps", type=float, nargs="+", default=[0.0, Config().clip_strong_gap])
    ap.add_argument("--only", choices=list(INDEXES), nargs="*", default=list(INDEXES))
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # the arrows, on a Windows console
    result = {}
    for name in args.only:
        result[name] = run_index(name, *INDEXES[name], args.gaps)
        for b in args.gaps[1:]:
            result[name][f"changes {args.gaps[0]} → {b}"] = changes(result[name], args.gaps[0], b)
            for set_name, rows in result[name][f"changes {args.gaps[0]} → {b}"].items():
                for r in rows:
                    print(f"  {name} {set_name} {r['id']} ({r['type']}): rank {r['rank']}, fired {r['fired']}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "check_gap.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT / 'check_gap.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
