"""Item w: the screenshot-vs-photo classifier (SPEC §6.2 rule 4) measured on the real files. Measurement only.

    python bench/kind_real.py

Every image in the eval index from `corpus/real/` (public) and the private eval set gets a label from its name or
EXIF: "Screenshot…" is a screenshot; "IMG_…" or a camera in EXIF is a photo; any other image has no label and is
only counted. The CLIP rule runs on each labelled image's stored CLIP vector on its own, as if no earlier rule had
decided (a name or EXIF decides these files in the app, so the app's kind for them is right by construction; the
rule matters for the images without either, such as forwarded photos). Writes eval/results_kind.md and .json;
private files by id only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from vault import eval as ev, models  # noqa: E402
from vault.config import Config  # noqa: E402
from vault.ingest.classify import PHOTO_PROMPTS, SCREENSHOT_PROMPTS, prompt_vectors  # noqa: E402
from vault.ingest.meta import read_image_meta  # noqa: E402
from vault.search.retrievers import load_image_index  # noqa: E402


def label(name: str, path: Path) -> str | None:
    if name.lower().startswith("screenshot"):
        return "screenshot"
    if name.startswith("IMG_"):
        return "photo"
    meta = read_image_meta(path)
    return "photo" if meta is not None and meta.camera else None


def main() -> int:
    cfg = Config()
    paths = ev.Paths()
    ids = ev.private_ids(paths)
    lib = ev.eval_library(cfg, paths, index=False)
    clip = models.clip_text(cfg)
    s_vecs, p_vecs = prompt_vectors(clip.embed_text, SCREENSHOT_PROMPTS), prompt_vectors(clip.embed_text, PHOTO_PROMPTS)
    rows = []
    with lib.connect() as conn:
        index = load_image_index(conn)
        kinds = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, kind, kind_reason FROM files")}
        for file_id, vec in zip(index.ids.tolist(), index.matrix):
            path = Path(conn.execute("SELECT path FROM paths WHERE file_id = ? ORDER BY mtime DESC LIMIT 1",
                                     (file_id,)).fetchone()[0])
            key = ev.key_of(str(path), paths, ids)
            if not (key.startswith("corpus/real/") or ev.is_private(key)):
                continue  # the generated corpus: not real files
            lead = float(np.mean(s_vecs @ vec) - np.mean(p_vecs @ vec))
            rows.append({"file": key, "set": "private" if ev.is_private(key) else "public",
                         "label": label(path.name, path), "lead": round(lead, 4),
                         "clip": "screenshot" if lead >= cfg.clip_screenshot_margin else "photo",
                         "app_kind": kinds[file_id][0], "app_reason": kinds[file_id][1].split(":")[0]})

    def acc(rs: list[dict]) -> dict:
        right = sum(r["clip"] == r["label"] for r in rs)
        return {"n": len(rs), "right": right, "pct": round(100 * right / len(rs), 1) if rs else None}

    labelled = [r for r in rows if r["label"]]
    out: dict = {"margin": cfg.clip_screenshot_margin, "prompts": [SCREENSHOT_PROMPTS, PHOTO_PROMPTS], "results": {}}
    md = ["# Screenshot vs photo on the real files (item w)", "",
          f"CLIP rule alone (SPEC §6.2 rule 4) at `clip_screenshot_margin` = {cfg.clip_screenshot_margin}, on each "
          "image's stored CLIP vector. Labels from names and EXIF: \"Screenshot…\" is a screenshot; \"IMG_…\" or a "
          "camera in EXIF is a photo. Measurement only: no value changed.", "",
          "| set | label | n | CLIP right | lead (screenshot − photo cosine): min / median / max |", "|---|---|---|---|---|"]
    for s in ("all", "public", "private"):
        for lab in ("screenshot", "photo", "both"):
            rs = [r for r in labelled if (s == "all" or r["set"] == s) and (lab == "both" or r["label"] == lab)]
            a = acc(rs)
            out["results"][f"{s}/{lab}"] = {**a, "lead": [min(r["lead"] for r in rs), float(np.median(
                [r["lead"] for r in rs])), max(r["lead"] for r in rs)] if rs else None}
            lead = out["results"][f"{s}/{lab}"]["lead"]
            md.append(f"| {s} | {lab} | {a['n']} | " + (f"{a['right']} ({a['pct']:.0f}%) | {lead[0]:.3f} / "
                                                          f"{lead[1]:.3f} / {lead[2]:.3f} |" if rs else "– | – |"))
    unl = [r for r in rows if not r["label"]]
    out["unlabelled"] = {"n": len(unl), "clip_screenshot": sum(r["clip"] == "screenshot" for r in unl),
                         "by_set": {s: sum(r["set"] == s for r in unl) for s in ("public", "private")}}
    by_rule = {r["app_reason"] for r in labelled}
    md += ["", f"In the app, the labelled images were decided by {', '.join(sorted(by_rule))} (rules 1-2), so their kind "
           f"is right there; the unlabelled ones by "
           f"{', '.join(sorted({r['app_reason'] for r in unl}))}: {sum(r['app_kind'] == 'screenshot' for r in unl)} "
           f"screenshots, {sum(r['app_kind'] == 'photo' for r in unl)} photos."]
    wrong = [r for r in labelled if r["clip"] != r["label"]]
    md += ["", f"Images without a label (no \"Screenshot\" or \"IMG_\" name, no camera EXIF; forwarded chat images): "
           f"{len(unl)} ({out['unlabelled']['by_set']['public']} public, {out['unlabelled']['by_set']['private']} "
           f"private); CLIP calls {out['unlabelled']['clip_screenshot']} of them screenshots. No label, so no accuracy.",
           "", f"Labelled images CLIP gets wrong ({len(wrong)}):", "", "| file | set | label | CLIP | lead |",
           "|---|---|---|---|---|"]
    md += [f"| {r['file']} | {r['set']} | {r['label']} | {r['clip']} | {r['lead']:.3f} |" for r in wrong]
    out["wrong"] = wrong
    out["files"] = rows
    target = ev.Paths().out / "results_kind"
    target.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    target.with_suffix(".json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
