"""Calibrate the provisional search thresholds on generated data (CLAUDE.md, SPEC §9 tuning rule).

    python bench/calibrate.py dense     # dense_min_cosine: bge cosines of matching vs unrelated pairs
    python bench/calibrate.py clip      # CLIP's top hit (z and cosine): drawn scenes vs queries no picture answers
    python bench/calibrate.py rule      # clip_strong_z / clip_strong_cosine end to end: scene and paraphrase recall

Each prints a summary and writes every score to bench/generated/calibrate_<name>.json. Nothing here comes from
anyone's personal files: the texts are written below and the pictures are drawn by `scenes.py`. The values in
`vault/config.py` are provisional until M6 tunes them on the eval's `tune` half.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

import numpy as np  # noqa: E402

from vault import models  # noqa: E402
from vault.config import Config  # noqa: E402

OUT = ROOT / "bench" / "generated"

# ---------------------------------------------------------------- dense: chunks and queries

# Chunk-sized texts in the styles the index holds: OCR'd screenshots, PDF pages, slides, notes, receipts, and
# three ~800-character chunks (a lecture transcript, a paper introduction, household notes) where the match is
# one part of the chunk.
DOCS: dict[str, str] = {
    "svd": "Definition 4.2. Every real m-by-n matrix A can be written as A = U S V^T, where U and V are orthogonal "
           "and S is diagonal with non-negative entries in decreasing order. The diagonal entries are called the "
           "singular values of A.",
    "mri": "We pretrain an encoder on 40,000 unlabeled brain MRI volumes using a self-supervised objective that "
           "pulls together two augmented views of the same scan and pushes apart views of different scans. "
           "Fine-tuned with only 5% of the annotations, the network outlines tumours voxel by voxel as accurately "
           "as a fully supervised baseline.",
    "diffusion": "We train a neural network to reverse a gradual noising process. Starting from pure Gaussian "
                 "noise, the model removes a little noise at each of 1,000 steps until a clean sample remains, "
                 "producing high-quality pictures without adversarial training.",
    "gd": "Gradient descent. Update the weights by stepping against the gradient: w <- w - eta * grad L(w). The "
          "learning rate eta sets the step size; too large and the loss diverges.",
    "refund": "If the carrier withdraws your journey, you are entitled to your money back within seven days. "
              "Submit the claim form with your booking reference and bank details.",
    "bill": "TANGEDCO Consumer No 0412 0567 891 Bill date 03-10-2025 Units consumed 284 Amount payable "
            "Rs 2,340.00 Pay before 05-11-2025 to avoid a late payment surcharge",
    "error": "Destination Folder Access Denied You need permission to perform this action Error 0x80070005: "
             "Access is denied. Try Again Cancel",
    "traceback": "Traceback (most recent call last): File \"server.py\", line 41, in load_settings "
                 "port = config['port'] KeyError: 'port'",
    "sourdough": "Sourdough: mix 500 g flour, 350 g water and 100 g leaven. Rest an hour, fold four times, shape, "
                 "then proof overnight in the fridge and bake at 230 C.",
    "ticket": "ELECTRONIC RESERVATION SLIP PNR 4521873390 Train 10111 Konkan Kanya Express From Mumbai CSMT To "
              "Madgaon Coach S4 Berth 32 Status CNF Departure 23:05",
    "wifi": "Network name: Acme-Guest Security: WPA2 Key: tulip-4417-river Ask reception if it stops working",
    "heart": "Chest pain spreading to the left arm or jaw, shortness of breath and cold sweats can signal a "
             "myocardial infarction. Call an ambulance at once and chew an aspirin unless you are allergic.",
    "bike": "Take the wheel off, lever the tube out of the rim, and find the puncture by dunking it in water and "
            "watching for bubbles. Patch it, refit, and pump to the pressure printed on the sidewall.",
    "overfit": "When validation loss climbs while training loss keeps falling, the model is memorising. Add "
               "dropout or weight decay, augment the data, or stop early.",
    "photosynthesis": "Chlorophyll in the leaves absorbs sunlight and turns carbon dioxide and water into glucose, "
                      "giving off oxygen as a by-product.",
    "contract": "12. Termination. Either party may end this agreement by giving the other sixty days' written "
                "notice. Fees already paid are not refundable.",
    "meeting": "Priya: heads up, our weekly catch-up with the design folks is pushed back two days. Same time, "
               "room 4B.",
    "battery": "My notebook only lasts about an hour unplugged now; the charge drops from full to empty while I'm "
               "just browsing.",
    "rent": "Received from Mr A. Kumar the sum of Rs 18,000 towards house rent for October 2025. Landlord: "
            "S. Iyer (signature).",
    "vaccine": "Your child's next immunisation is due at 18 months: the booster dose of DTP and the second MMR "
               "shot. Bring the record card to the clinic.",
    "git": "$ git push origin main ! [rejected] main -> main (fetch first) error: failed to push some refs to "
           "'github.com:acme/site.git' hint: Updates were rejected because the remote contains work that you do "
           "not have locally.",
    "lecture": "Okay so before we start, the assignment deadline moved to Friday, and the lab is in room 204 this "
               "week. Right, where were we. Last time we looked at symmetric matrices, and the key fact today is "
               "that a real symmetric matrix always has real eigenvalues, and you can pick its eigenvectors to be "
               "orthogonal. So that's the spectral theorem. Why does this matter? Because it means you can "
               "diagonalise the matrix with a rotation, and that's exactly what principal component analysis "
               "does with the covariance matrix. Someone asked about the midterm: it covers chapters one to four, "
               "no calculators. Okay, let's do an example on the board. Take the two by two matrix with twos on "
               "the diagonal and ones off it, and let's find its eigenvalues together.",
    "attention": "Recurrent networks process tokens one after another, which makes training slow and long-range "
                 "dependencies hard to learn. We propose an architecture that drops recurrence entirely and "
                 "relies on attention alone: every position looks at every other position in a single step, "
                 "weighted by learned similarity scores. Stacking six such layers in an encoder and six in a "
                 "decoder, the model reaches a new state of the art on English-to-German translation after "
                 "twelve hours on eight GPUs, at a fraction of the training cost of earlier systems. We also show "
                 "that the same architecture generalises to constituency parsing.",
    "house_notes": "House notes. The plumber is coming on Saturday to look at the kitchen tap. Garbage "
                   "collection is on Tuesdays and Fridays; recycling goes out on the first Monday of the month. "
                   "The spare key is with the neighbour at 14B. The internet router is in the hall cupboard; "
                   "restart it by holding the button for ten seconds. Water the balcony plants every other day in "
                   "summer. The gas cylinder was replaced on 2 October; book the next one around mid-November.",
}

# (query, target doc, difficulty). "hard": the same meaning in almost entirely different words.
PAIRS: list[tuple[str, str, str]] = [
    ("singular value decomposition definition", "svd", "easy"),
    ("splitting any array of numbers into two rotations and a stretch", "svd", "hard"),
    ("contrastive learning for medical image segmentation", "mri", "hard"),
    ("denoising diffusion models", "diffusion", "easy"),
    ("generative model that synthesises photos step by step", "diffusion", "hard"),
    ("slide about gradient descent", "gd", "easy"),
    ("how an optimiser adjusts parameters to minimise the error", "gd", "hard"),
    ("refund for a cancelled flight", "refund", "hard"),
    ("electricity bill", "bill", "easy"),
    ("how much do I owe the power company this month", "bill", "hard"),
    ("access denied error", "error", "easy"),
    ("not allowed to copy files into that directory", "error", "hard"),
    ("python traceback KeyError", "traceback", "easy"),
    ("program crashed because a dictionary entry was missing", "traceback", "hard"),
    ("recipe for homemade bread", "sourdough", "hard"),
    ("Konkan Kanya Express reservation", "ticket", "easy"),
    ("my railway booking for the overnight journey", "ticket", "hard"),
    ("office internet password", "wifi", "hard"),
    ("heart attack warning signs", "heart", "hard"),
    ("fixing a flat bicycle tyre", "bike", "hard"),
    ("ways to prevent overfitting in neural networks", "overfit", "hard"),
    ("how plants make food", "photosynthesis", "hard"),
    ("contract termination clause", "contract", "easy"),
    ("how much warning is needed to cancel the contract", "contract", "hard"),
    ("team meeting postponed", "meeting", "hard"),
    ("laptop battery drains fast", "battery", "hard"),
    ("rent receipt October", "rent", "easy"),
    ("proof that I paid the landlord", "rent", "hard"),
    ("kid's vaccination schedule", "vaccine", "hard"),
    ("can't upload my commits because the server has newer changes", "git", "hard"),
    ("eigenvalues of symmetric matrices are real", "lecture", "easy"),
    ("self-adjoint operators can be diagonalised by an orthogonal change of basis", "lecture", "hard"),
    ("attention replaces recurrence in translation models", "attention", "easy"),
    ("paper introducing the transformer", "attention", "hard"),
    ("when is trash pickup", "house_notes", "hard"),
]

# Visual queries: no text chunk answers them, so every pair is unrelated (what the floor is meant to drop).
VISUAL = [
    "sunset at the beach", "three women standing outside", "handwritten notes on a black background",
    "a cat sleeping on a sofa", "dice on a table", "a chessboard", "red car parked on the street",
    "snow on the mountains", "group photo at a wedding", "a dog playing in the park",
]

DENSE_SPREADS = 2.0  # the floor sits this many standard deviations of the hard-pair scores below the lowest match


def stats(xs: list[float]) -> dict[str, float]:
    a = np.asarray(xs)
    return {"n": len(xs), "min": round(float(a.min()), 4), "p50": round(float(np.median(a)), 4),
            "p95": round(float(np.percentile(a, 95)), 4), "max": round(float(a.max()), 4)}


def dense() -> dict:
    cfg = Config()
    emb = models.text_embedder(cfg)
    names = list(DOCS)
    D = emb.embed_documents([DOCS[n] for n in names])
    match: dict[str, list[float]] = {"easy": [], "hard": []}
    cross, rows = [], []
    for q, target, level in PAIRS:
        s = D @ emb.embed_query(q)
        for n, score in zip(names, s):
            rows.append({"query": q, "doc": n, "cosine": float(score), "match": n == target, "level": level})
            (match[level] if n == target else cross).append(float(score))
    visual = []
    for q in VISUAL:
        s = D @ emb.embed_query(q)
        visual += [float(x) for x in s]
        rows += [{"query": q, "doc": n, "cosine": float(x), "match": False, "level": "visual"} for n, x in zip(names, s)]
    lowest = min(match["easy"] + match["hard"])
    margin = round(DENSE_SPREADS * float(np.std(match["hard"])), 3)
    floor = math.floor((lowest - margin) * 100) / 100  # rounded down: too low only lets more weak hits through
    lowest_pair = min((r for r in rows if r["match"]), key=lambda r: r["cosine"])
    summary = {
        "model": emb.name, "docs": len(names),
        "matching_easy": stats(match["easy"]), "matching_hard": stats(match["hard"]),
        "lowest_matching_pair": {k: lowest_pair[k] for k in ("query", "doc", "cosine")},
        "non_matching_text_queries": stats(cross), "visual_queries": stats(visual),
        "margin": margin, "floor": floor, "headroom_on_lowest_match": round(lowest - floor, 4),
        "visual_pairs_cut": round(float(np.mean(np.asarray(visual) < floor)), 3),
        "non_matching_text_pairs_cut": round(float(np.mean(np.asarray(cross) < floor)), 3),
        "visual_pairs_above_lowest_match": int(np.sum(np.asarray(visual) >= lowest)),
        "cut_rate_by_floor": {str(f): {"visual": round(float(np.mean(np.asarray(visual) < f)), 3),
                                       "non_matching_text": round(float(np.mean(np.asarray(cross) < f)), 3),
                                       "matching": int(np.sum(np.asarray(match["easy"] + match["hard"]) < f))}
                              for f in (0.40, 0.42, 0.45, 0.48, 0.50)},
    }
    return {"summary": summary, "rows": rows}


# ---------------------------------------------------------------- clip: strong outliers

TEXT_QUERIES = [  # answered by no picture
    "electricity invoice", "error 0x80070005", "python traceback KeyError", "singular value decomposition",
    "meeting notes from Monday", "train ticket PNR", "rent receipt October", "gradient descent learning rate",
    "contract termination notice", "wifi password",
]
VISUAL_MISSES = [  # visual, but no picture in the set shows it
    "three women standing outside", "a cat sleeping on a sofa", "a dog playing in the park",
    "handwritten notes on a black background", "red car parked on the street", "group photo at a wedding",
]


def _clip_collection(n_extra: int) -> tuple[list[str], list]:
    """The 12 scenes plus `n_extra` distractors: text-free photos, desktop UI, phone chats, code editors."""
    import scenes

    names = [n for n, _, _ in scenes.SCENES]
    images = [draw() for _, draw, _ in scenes.SCENES]
    makers = [scenes.noise_photo, scenes.desktop_screenshot, scenes.phone_chat, scenes.code_editor]
    for i in range(n_extra):
        make = makers[i % len(makers)]
        names.append(f"{make.__name__}-{i}")
        images.append(make(1000 + i))
    return names, images


def _top(scores: np.ndarray) -> tuple[int, float, float]:
    i = int(np.argmax(scores))
    return i, float(scores[i]), float((scores[i] - scores.mean()) / scores.std())


def clip() -> dict:
    import scenes
    from vault.ingest.extract_image import clip_input

    cfg = Config()
    vision, text = models.clip_vision(cfg), models.clip_text(cfg)
    rows, summary = [], {"model": vision.name}
    for n_extra in (24, 188):  # 36 and 200 candidates: the z-gate runs
        names, images = _clip_collection(n_extra)
        V = vision.embed_images([clip_input(im, cfg.clip_side) for im in images])
        hit_z, miss_z, hits = [], [], 0
        for name, _, q in scenes.SCENES:
            i, cos, z = _top(V @ text.embed_text(q))
            hits += names[i] == name
            target = names.index(name)
            s = V @ text.embed_text(q)
            tz = float((s[target] - s.mean()) / s.std())
            hit_z.append(tz)
            rows.append({"n": len(names), "query": q, "kind": "scene", "top": names[i], "top_cos": cos, "top_z": z,
                         "target_z": tz, "target_cos": float(s[target])})
        for kind, qs in (("text", TEXT_QUERIES), ("visual-miss", VISUAL_MISSES)):
            for q in qs:
                i, cos, z = _top(V @ text.embed_text(q))
                miss_z.append(z)
                rows.append({"n": len(names), "query": q, "kind": kind, "top": names[i], "top_cos": cos, "top_z": z})
        summary[f"n={len(names)}"] = {"scene_top1": f"{hits}/{len(scenes.SCENES)}", "scene_target_z": stats(hit_z),
                                      "no_match_top_z": stats(miss_z)}
    # Fewer than 20 candidates: each scene alone with 4 distractors (the fallback has no z, only cosine)
    names, images = _clip_collection(4 * len(scenes.SCENES))
    V = vision.embed_images([clip_input(im, cfg.clip_side) for im in images])
    match, miss = [], []
    for k, (name, _, q) in enumerate(scenes.SCENES):
        idx = [k] + list(range(len(scenes.SCENES) + 4 * k, len(scenes.SCENES) + 4 * k + 4))
        s = V[idx] @ text.embed_text(q)
        match.append(float(s[0]))
        miss += [float(x) for x in s[1:]]
        for q2 in TEXT_QUERIES + VISUAL_MISSES:
            miss += [float(x) for x in V[idx] @ text.embed_text(q2)]
    summary["small: scene vs its query cosine"] = stats(match)
    summary["small: every non-matching pair cosine"] = stats(miss)
    summary["small: non-matching pairs at or above each cosine"] = {
        str(c): int(np.sum(np.asarray(miss) >= c)) for c in (0.26, 0.27, 0.28, 0.29, 0.30)}
    summary["small: matching pairs at or above each cosine"] = {
        str(c): int(np.sum(np.asarray(match) >= c)) for c in (0.26, 0.27, 0.28, 0.29, 0.30)}
    return {"summary": summary, "rows": rows}


# ---------------------------------------------------------------- rule: the visual-query rule end to end

# One text file per scene query that shares a word with it but isn't about it: the real-file failure pattern
# (a file matching 1-2 common words through BM25 and dense outranked CLIP's strong hit).
WEAK_WORD_DOCS = {
    "sunset": "Train to Goa leaves at 6 am; pack sunscreen and a hat for the beach.",
    "chess": "The chess club meets on Thursdays in the library; bring your own board.",
    "field": "Fill in the blue form and return it to the field office by Friday.",
    "mountains": "The mountains trip is cancelled; refunds go out next week.",
    "moon": "Night shift starts at 10 pm; sign in at the front desk.",
    "dice": "Book a table for four at 8 pm under the name Rao.",
    "traffic": "Traffic on the ring road is heavy; leave by 7 to catch the flight.",
    "apple": "Apple ID verification code: enter it within ten minutes.",
    "house": "House rules: no shoes inside, and the last one out locks the door.",
    "rainbow": "Sky Broadband: your bill for October is ready to view.",
    "chart": "Bar exam results are published on the council website on Monday.",
    "smiley": "Face ID failed; enter your passcode to continue.",
}
STRONG_Z = [3.0, 3.25, 3.5, 3.75, 4.0, None]  # None: the rule never fires (today's ranking)
STRONG_COS = [0.27, 0.28, 0.29, 0.30, None]


class _NoOcr:
    """Images stay text-free: the rule is about CLIP against the text files."""

    name = "no-ocr"

    def read(self, image, *, use_cls=None):
        return []


def _rank(results, filename: str) -> int | None:
    return next((i for i, r in enumerate(results, start=1) if r.filename == filename), None)


def _library(root: Path, n_extra: int) -> dict[str, str]:
    """Write the files under neutral names (a name like `sunset.png` would match its query through the path
    chunk) and return {content name: filename}."""
    root.mkdir(parents=True)
    files: dict[str, str] = {}
    for i, (name, text) in enumerate({**DOCS, **{f"weak-{k}": v for k, v in WEAK_WORD_DOCS.items()}}.items()):
        files[name] = f"doc_{i:03}.txt"
        (root / files[name]).write_text(text, encoding="utf-8")
    names, images = _clip_collection(n_extra)
    for i, (name, img) in enumerate(zip(names, images)):
        files[name] = f"IMG_{i:04}.png"
        img.save(root / files[name])
    return files


def rule() -> dict:
    import shutil
    from dataclasses import replace

    import scenes
    from vault import db
    from vault.ingest.pipeline import index_paths
    from vault.search.engine import search

    models.set_ocr_engine(_NoOcr())
    rows, summary = [], {}
    for n_extra, levels, field in ((24, STRONG_Z, "clip_strong_z"), (188, STRONG_Z, "clip_strong_z"),
                                   (4, STRONG_COS, "clip_strong_cosine")):
        work = OUT / f"rule_{n_extra}"
        shutil.rmtree(work, ignore_errors=True)
        files = _library(work / "files", n_extra)
        base = Config(data_dir=work / "data")
        conn = db.connect(base.db_path)
        index_paths(conn, [work / "files"], base)
        n_images = len(scenes.SCENES) + n_extra
        table = {}
        for level in levels:
            cfg = replace(base, **{field: level if level is not None else float("inf")})
            scene_r = [_rank(search(conn, q, cfg).results, files[name]) for name, _, q in scenes.SCENES]
            para_r = [_rank(search(conn, q, cfg).results, files[target]) for q, target, _ in PAIRS]
            fired = sum(search(conn, q, cfg).clip["visual"] for q, _, _ in PAIRS)
            table[str(level or "off")] = {
                "scene_R@1": sum(r == 1 for r in scene_r), "scene_R@5": sum(r is not None and r <= 5 for r in scene_r),
                "para_R@1": sum(r == 1 for r in para_r),
                "para_MRR": round(sum(1 / r for r in para_r if r) / len(para_r), 3),
                "rule_fired_on_paraphrases": fired,
            }
            rows.append({"images": n_images, field: level, "scene_ranks": scene_r, "para_ranks": para_r})
        conn.close()
        summary[f"{n_images} images ({len(scenes.SCENES)} scenes), {field}"] = table
    summary["queries"] = {"scenes": len(scenes.SCENES), "paraphrases": len(PAIRS)}
    return {"summary": summary, "rows": rows}


def main(argv: list[str]) -> None:
    jobs = {"dense": dense, "clip": clip, "rule": rule}
    if not argv or argv[0] not in jobs:
        sys.exit(f"usage: python bench/calibrate.py {{{'|'.join(jobs)}}}")
    out = jobs[argv[0]]()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"calibrate_{argv[0]}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out["summary"], indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
