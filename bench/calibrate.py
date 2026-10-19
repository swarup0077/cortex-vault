"""Calibrate the provisional search thresholds on generated data (CLAUDE.md, SPEC §9 tuning rule).

    python bench/calibrate.py dense     # dense_min_cosine: bge cosines of matching vs unrelated pairs
    python bench/calibrate.py tiny      # dense_min_word: where very short chunks land for queries they do not answer
    python bench/calibrate.py prose     # snippet_prose_words: where a meaning-only snippet starts on OCR chunks
    python bench/calibrate.py toc       # toc_min_entries: dot-leader entries on contents pages vs other pages
    python bench/calibrate.py clip      # CLIP's top hit (z and cosine): drawn scenes vs queries no picture answers
    python bench/calibrate.py rule      # clip_strong_z / clip_strong_cosine end to end: scene and paraphrase recall
    python bench/calibrate.py kind      # clip_screenshot_margin: generated screen captures vs photo-like pictures
    python bench/calibrate.py ocr-stop  # pdf_ocr_stop_*: OCR readability of printed vs handwriting-like scanned pages

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


# ---------------------------------------------------------------- tiny chunks (open item x)

# What OCR leaves on text-free photos and UI icons (one- and two-character lines, stray ideographs, status-bar bits)
JUNK = ["0", "M", "8", "x", "|", "日", "品", "0 田", "白 这", "ll", "I1", "oO", "Ok", "5G", "Wi", "@ #", "1 2", "87%",
        "8:12", "TRU", "NOTE", "x 2", "ii I", "B2", "-_-", "OO 0", "m n", "ON"]
# Short chunks that do mean something: slide titles, notice headings, receipt lines
SHORT = ["Summary", "Questions", "Agenda", "Welcome", "Pooling", "Thank you", "Lab 3", "Q-learning", "Office hours",
         "Hostel notice", "LIBRARY NOTICE", "Payment successful", "Low Disk Space", "404 Not Found", "Masala dosa",
         "TOTAL Rs 456.75", "Convolution", "Exit", "Rainbow", "Reading list"]


def _chars(text: str) -> int:
    return sum(not c.isspace() for c in text)


TINY_LENGTHS = range(1, 21)
TINY_PER_LENGTH = 12  # junk strings and real fragments per length


def _tiny_samples() -> tuple[list[str], list[str]]:
    """For every length 1-20 (non-space characters): OCR-like junk drawn from digits, letters, punctuation and a
    few ideographs, and real fragments: the start of a calibration document cut to that length."""
    import random

    rnd = random.Random(7)
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz|-_:%@#.,日品白这田"
    texts, kinds = list(JUNK) + list(SHORT), ["junk"] * len(JUNK) + ["short"] * len(SHORT)
    starts = [t for doc in DOCS.values() for t in doc.split(". ") if _chars(t) >= 30]
    for n in TINY_LENGTHS:
        for _ in range(TINY_PER_LENGTH):
            words, s = [], ""
            while _chars(s) < n:  # junk: short "words" of random characters
                words.append("".join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 4))))
                s = " ".join(words)
            texts.append(s if _chars(s) == n else s.rstrip()[: len(s) - (_chars(s) - n)])
            kinds.append("junk")
            src, cut = rnd.choice(starts), ""
            for ch in src[rnd.randint(0, 10):] if len(src) > 40 else src:  # real: a fragment of a sentence
                if _chars(cut) == n and not ch.isspace():
                    break
                cut += ch
            texts.append(cut.strip())
            kinds.append("short")
    return texts, kinds


def tiny() -> dict:
    """Where very short chunks land in dense search for queries they don't answer, by their non-space characters.
    A chunk with almost no text embeds near the middle of the space, so it sits high for many queries."""
    cfg = Config()
    emb = models.text_embedder(cfg)
    names = list(DOCS)
    extra, extra_kinds = _tiny_samples()
    texts = [DOCS[n] for n in names] + extra
    kinds = ["doc"] * len(names) + extra_kinds
    D = emb.embed_documents(texts)
    groups = {"text": [q for q, _, _ in PAIRS], "visual": VISUAL}  # visual: no text chunk answers them
    rows = [{"text": t, "kind": k, "chars": _chars(t)} for t, k in zip(texts, kinds)]
    n_docs = len(names)
    for g, queries in groups.items():
        top5, floor = np.zeros(len(texts)), np.zeros(len(texts))
        for q in queries:
            s = D @ emb.embed_query(q)
            docs_s = s[:n_docs]
            # Each string on its own among the real chunks (a doc among the other docs): would it reach the top 5?
            for i, score in enumerate(s):
                others = np.delete(docs_s, i) if i < n_docs else docs_s
                top5[i] += int(np.sum(others > score)) < 5
            floor += s >= cfg.dense_min_cosine
        for r, a, b in zip(rows, top5, floor):
            r[f"{g}_top5"] = round(float(a / len(queries)), 3)
            r[f"{g}_above_floor"] = round(float(b / len(queries)), 3)

    def mean(sel: list[dict], key: str) -> float:
        return round(float(np.mean([r[key] for r in sel])), 3)

    for r in rows:
        r["longest_word"] = _longest_word(r["text"])
    docs = [r for r in rows if r["kind"] == "doc"]
    keys = [f"{g}_{m}" for g in groups for m in ("top5", "above_floor")]
    by_chars = {}
    for n in sorted({r["chars"] for r in rows if r["kind"] != "doc"}):
        sel = [r for r in rows if r["kind"] != "doc" and r["chars"] == n]
        by_chars[n] = {"strings": len(sel), **{k: mean(sel, k) for k in keys}}
    by_word = {}
    for n in sorted({min(r["longest_word"], 6) for r in rows if r["kind"] != "doc"}):
        sel = [r for r in rows if r["kind"] != "doc" and min(r["longest_word"], 6) == n]
        by_word["6+" if n == 6 else str(n)] = {"strings": len(sel), "junk": sum(r["kind"] == "junk" for r in sel),
                                               **{k: mean(sel, k) for k in keys}}
    # The rule: a chunk with no word (run of letters or digits) at least `min_word` long gets no vector. What it
    # removes from dense: junk strings, and real fragments of 3+ characters (those would be a loss).
    rule = {}
    for w in (2, 3, 4):
        junk = [r for r in rows if r["kind"] == "junk"]
        real = [r for r in rows if r["kind"] == "short" and r["chars"] >= 3]
        rule[str(w)] = {"junk_removed": round(float(np.mean([r["longest_word"] < w for r in junk])), 3),
                        "real_fragments_removed": round(float(np.mean([r["longest_word"] < w for r in real])), 3)}
    summary = {"model": emb.name, "queries": {g: len(q) for g, q in groups.items()}, "doc_chunks": len(docs),
               "docs": {k: mean(docs, k) for k in keys}, "by_non_space_chars": by_chars,
               "by_longest_word": by_word, "rule_min_word": rule}
    return {"summary": summary, "rows": rows}


def _contents_text(seed: int, entries: int, leader: str) -> str:
    from bench_ocr import sentence
    import random

    rnd = random.Random(seed)
    rows = [f"{i + 1}  {sentence(rnd, rnd.randint(2, 4))} {leader * rnd.randint(8, 20)} {6 * i + 1}"
            for i in range(entries)]
    return "\n".join(["Contents", *rows])


def _ocr_text(img) -> str:
    """What OCR reads off a drawn page, lines joined as a chunk joins them."""
    lines = models.ocr_engine(Config()).read(np.asarray(img.convert("RGB")))
    return " ".join(ln.text for ln in sorted(lines, key=lambda ln: (round(ln.y0 / 20), ln.x0)) if ln.conf >= 0.5)


def _drawn(text: str, seed: int):
    from PIL import ImageDraw

    from bench_ocr import font

    img = _printed(seed, lines=0)
    d = ImageDraw.Draw(img)
    for i, line in enumerate(text.split("\n")):
        d.text((110, 130 + 62 * i), line, fill=(25, 25, 25), font=font(30 if i else 44))
    return img


def toc() -> dict:
    """toc_min_entries: dot-leader entries ("Title ........ 17") on generated contents pages, as native text and as
    real OCR reads them, against pages that also have dots and numbers: receipts, exam papers, prose with ellipses."""
    import random

    from bench_ocr import sentence
    from vault.search.toc import leader_entries

    rnd = random.Random(11)
    pages: list[tuple[str, str, int, str]] = []  # (kind, form, entries drawn, text)
    for i, n in enumerate([3, 4, 5, 6, 7, 8, 10, 12, 14] * 2):
        leader = [".", ". ", "…"][i % 3]
        text = _contents_text(100 + i, n, leader)
        pages.append(("contents", "native", n, text))
        if leader != "…":  # the font has no ellipsis glyph
            pages.append(("contents", "ocr", n, _ocr_text(_drawn(text, 200 + i))))
    others = []
    for i in range(6):
        others.append(("prose", " ".join(sentence(rnd, rnd.randint(6, 12)) + "." for _ in range(14))))
    for i in range(4):
        items = [f"{sentence(rnd, 2)} {'.' * rnd.randint(6, 14)} {rnd.randint(40, 400)}.00" for _ in range(rnd.randint(3, 7))]
        others.append(("receipt", "\n".join(["Receipt", *items, f"TOTAL ........ {rnd.randint(500, 900)}.00"])))
    for i in range(4):
        qs = [f"Q{j + 1}. {sentence(rnd, 8)} ......... ({rnd.choice([2, 5, 10])} marks)" for j in range(rnd.randint(4, 8))]
        others.append(("exam", "\n".join(["Answer all questions", *qs])))
    for i in range(4):
        others.append(("ellipses", " ".join(f"{sentence(rnd, 7)}... and {rnd.randint(2, 9)} more" for _ in range(6))))
    for kind, text in list(others):
        pages.append((kind, "native", 0, text))
        pages.append((kind, "ocr", 0, _ocr_text(_drawn(text, rnd.randint(300, 900)))))
    rows = [{"kind": k, "form": f, "entries_drawn": n, "counted": leader_entries(t)} for k, f, n, t in pages]
    contents = [r for r in rows if r["kind"] == "contents"]
    rest = [r for r in rows if r["kind"] != "contents"]
    worst_other = max(r["counted"] for r in rest)
    by_threshold = {t: {"contents_flagged": sum(r["counted"] >= t for r in contents),
                        "others_flagged": sum(r["counted"] >= t for r in rest)} for t in range(2, 9)}
    summary = {"contents_pages": len(contents), "other_pages": len(rest),
               "most_entries_on_another_page": worst_other,
               "others_by_kind": {k: max(r["counted"] for r in rest if r["kind"] == k)
                                  for k in sorted({r["kind"] for r in rest})},
               "contents_counted_vs_drawn": [(r["form"], r["entries_drawn"], r["counted"]) for r in contents],
               "by_threshold": by_threshold}
    return {"summary": summary, "rows": rows}


def prose() -> dict:
    """snippet_prose_words: on the demo index's OCR chunks (all generated), where a meaning-only snippet starts.
    Chrome = what the generator draws above the content: a phone status bar ("08:12 87% Notes") or a window's title
    bar ("Documents Installer □ X", "Terminal - users", a browser's address)."""
    import re
    import sqlite3

    from vault.search.snippet import prose_start

    chrome = re.compile(r"^\s*(\d{1,2}:\d{2}\b|\S+ \S+ □ X|Terminal - |https?://|Settings\b)")
    conn = sqlite3.connect(ROOT / "demo_index" / "vault.db")
    texts = [t for (t,) in conn.execute("SELECT text FROM chunks WHERE source = 'ocr'")]
    out = {"chunks": len(texts), "starting_with_chrome_today": sum(bool(chrome.match(t)) for t in texts)}
    for run in (2, 3, 4):
        starts = [prose_start(t, run) for t in texts]
        rest = [t[s:] if s is not None else t for t, s in zip(texts, starts)]
        out[f"run_{run}"] = {"still_chrome": sum(bool(chrome.match(t)) for t in rest),
                             "no_prose": sum(s is None for s in starts),
                             "chars_skipped_p50": int(np.median([s or 0 for s in starts]))}
    return {"summary": out, "rows": []}


def _longest_word(text: str) -> int:
    """Characters in the longest run of letters or digits (an ideograph counts as one)."""
    import re

    return max((len(t) for t in re.findall(r"[^\W_]+", text)), default=0)


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


# ---------------------------------------------------------------- kind: screenshot vs photo by CLIP zero-shot

# Sizes a forwarded file arrives at (WhatsApp resizes to 1600 px on the long side), plus camera and screen sizes.
KIND_SIZES = [(720, 1600), (1600, 720), (1600, 900), (1200, 1600), (1600, 1200), (1080, 2400), (1920, 1080)]


def _fit(img, size: tuple[int, int]):
    """`img` scaled to cover `size` and centre-cropped to it, as a resize to a forwarded size would leave it."""
    from PIL import Image

    w, h = size
    scale = max(w / img.width, h / img.height)
    img = img.resize((max(w, round(img.width * scale)), max(h, round(img.height * scale))), Image.Resampling.BICUBIC)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def kind() -> dict:
    import scenes
    from vault.ingest.classify import PHOTO_PROMPTS, SCREENSHOT_PROMPTS, clip_kind, prompt_vectors
    from vault.ingest.extract_image import clip_input

    cfg = Config()
    vision, text = models.clip_vision(cfg), models.clip_text(cfg)
    S, P = prompt_vectors(text.embed_text, SCREENSHOT_PROMPTS), prompt_vectors(text.embed_text, PHOTO_PROMPTS)
    shots = [("desktop", lambda i, sz: scenes.desktop_screenshot(i)), ("chat", lambda i, sz: scenes.phone_chat(i, sz)),
             ("code", lambda i, sz: scenes.code_editor(i, sz)), ("text page", lambda i, sz: scenes.text_page(i, sz))]
    photos = [("noise photo", lambda i, sz: scenes.noise_photo(i, sz)), ("sign photo", lambda i, sz: scenes.sign_photo(i, sz)),
              ("scene", lambda i, sz: scenes.SCENES[i % len(scenes.SCENES)][1]())]
    rows = []
    for truth, makers in (("screenshot", shots), ("photo", photos)):
        for label, make in makers:
            for i, size in enumerate(KIND_SIZES * 2):  # two of each size, different seeds
                img = _fit(make(2000 + i, size), size)
                vec = vision.embed_images([clip_input(img, cfg.clip_side)])[0]
                lead = float(np.mean(S @ vec) - np.mean(P @ vec))
                rows.append({"truth": truth, "kind": label, "size": f"{size[0]}x{size[1]}", "lead": lead,
                             "default_margin_says": clip_kind(vec, S, P, cfg.clip_screenshot_margin)[0]})
    s_lead = [r["lead"] for r in rows if r["truth"] == "screenshot"]
    p_lead = [r["lead"] for r in rows if r["truth"] == "photo"]
    by_margin = {}
    for m in np.round(np.arange(0.0, 0.0751, 0.005), 3):
        right_s, right_p = int(sum(x >= m for x in s_lead)), int(sum(x < m for x in p_lead))
        by_margin[str(float(m))] = {"screenshots_right": right_s, "photos_right": right_p,
                             "balanced": round((right_s / len(s_lead) + right_p / len(p_lead)) / 2, 3)}
    per_kind = {}
    for r in rows:
        per_kind.setdefault(f'{r["truth"]}: {r["kind"]}', []).append(r["lead"])
    return {"summary": {
        "model": vision.name, "prompts": {"screenshot": SCREENSHOT_PROMPTS, "photo": PHOTO_PROMPTS},
        "screenshot_lead": stats(s_lead), "photo_lead": stats(p_lead),
        "lead_by_kind": {k: stats(v) for k, v in per_kind.items()},
        "right_by_margin": by_margin, "n": {"screenshots": len(s_lead), "photos": len(p_lead)},
    }, "rows": rows}


# ---------------------------------------------------------------- ocr-stop: early stop for unreadable PDFs (item k)

PAGE = (1240, 1754)  # an A4 page rendered at 150 DPI, as extract_pdf OCRs it


def _printed(seed: int, size: int = 28, lines: int | None = None, columns: int = 1) -> "Image.Image":
    from PIL import Image, ImageDraw

    from bench_ocr import font, sentence
    import random

    rnd = random.Random(seed)
    img = Image.new("RGB", PAGE, (244, 242, 236))
    d = ImageDraw.Draw(img)
    width = (PAGE[0] - 220) // columns
    for col in range(columns):
        y = 130
        n = 0
        while y < PAGE[1] - 150 and (lines is None or n < lines):
            d.text((110 + col * (width + 30), y), sentence(rnd, rnd.randint(5, 9 if columns == 1 else 5)),
                   fill=(25, 25, 25), font=font(size))
            y += int(size * 1.6)
            n += 1
    return img


def _title(seed: int) -> "Image.Image":
    from PIL import ImageDraw

    from bench_ocr import font, sentence
    import random

    rnd = random.Random(seed)
    img = _printed(seed, lines=0)
    d = ImageDraw.Draw(img)
    d.text((180, 600), sentence(rnd, 3), fill=(15, 15, 15), font=font(64))
    d.text((180, 720), sentence(rnd, 4), fill=(40, 40, 40), font=font(36))
    return img


def _contents(seed: int) -> "Image.Image":
    from PIL import ImageDraw

    from bench_ocr import font, sentence
    import random

    rnd = random.Random(seed)
    img = _printed(seed, lines=0)
    d = ImageDraw.Draw(img)
    d.text((110, 130), "Contents", fill=(15, 15, 15), font=font(48))
    for i in range(14):
        entry = f"{i + 1}  {sentence(rnd, rnd.randint(2, 4))} " + "." * rnd.randint(15, 30) + f" {8 * i + 1}"
        d.text((110, 240 + 70 * i), entry, fill=(25, 25, 25), font=font(30))
    return img


def _noisy(seed: int) -> "Image.Image":
    from PIL import Image, ImageFilter

    img = _printed(seed, size=26).rotate(0.8, fillcolor=(244, 242, 236)).filter(ImageFilter.GaussianBlur(1.3))
    arr = np.asarray(img, np.float32) + np.random.default_rng(seed).normal(0, 12, (PAGE[1], PAGE[0], 3))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _figure(seed: int) -> "Image.Image":
    from PIL import ImageDraw

    from bench_ocr import font, sentence
    import random

    rnd = random.Random(seed)
    img = _printed(seed, lines=0)
    d = ImageDraw.Draw(img)
    for i in range(6):
        h = rnd.randint(150, 600)
        d.rectangle((200 + 140 * i, 900 - h, 300 + 140 * i, 900), fill=(90, 120, 170))
    d.line((180, 900, 1100, 900), fill=(30, 30, 30), width=3)
    d.text((180, 960), "Figure 3. " + sentence(rnd, 8), fill=(25, 25, 25), font=font(28))
    d.text((180, 1010), sentence(rnd, 9), fill=(25, 25, 25), font=font(28))
    return img


def _scribble(seed: int, lined: bool = True) -> "Image.Image":
    """Handwriting-like ink: rows of connected loops and strokes with ascenders and descenders, on lined paper.
    Nothing a recogniser can read, which is what an untidy hand looks like to it."""
    from PIL import Image, ImageDraw
    import random

    rnd = random.Random(seed)
    img = Image.new("RGB", PAGE, (250, 250, 246))
    d = ImageDraw.Draw(img)
    if lined:
        for y in range(160, PAGE[1] - 80, 64):
            d.line((60, y, PAGE[0] - 60, y), fill=(180, 200, 230), width=2)
    ink = rnd.choice([(25, 35, 120), (20, 20, 20), (40, 60, 150)])
    for base in range(150, PAGE[1] - 120, 64):
        x = 100 + rnd.randint(0, 40)
        slant = rnd.uniform(0.15, 0.35)
        while x < PAGE[0] - 160:
            pts = []
            for _ in range(rnd.randint(3, 9)):  # one "word": a run of letter-like loops
                h = rnd.choice([16, 18, 20, 22, 38 if rnd.random() < 0.25 else 20])
                w = rnd.uniform(12, 22)
                down = 14 if rnd.random() < 0.15 else 0
                for t in np.linspace(0, 2 * math.pi, 9):
                    px = x + w / 2 + (w / 2) * math.cos(t + math.pi) + rnd.uniform(-1.5, 1.5)
                    py = base - h / 2 - (h / 2) * math.sin(t) + (down if t > math.pi else 0)
                    pts.append((px - slant * (py - base), py))
                x += w * rnd.uniform(0.8, 1.1)
            d.line(pts, fill=ink, width=3, joint="curve")
            x += rnd.uniform(25, 45)
    return img


def _neat_hand(seed: int) -> "Image.Image":
    """Real words, warped: per-word shear and rotation, a wavy baseline and a thicker pen. Partly readable."""
    from PIL import Image, ImageDraw, ImageFilter

    from bench_ocr import font, WORDS
    import random

    rnd = random.Random(seed)
    img = Image.new("L", PAGE, 250)
    for y in range(150, PAGE[1] - 130, 70):
        x = 110
        while x < PAGE[0] - 260:
            word = rnd.choice(WORDS)
            tile = Image.new("L", (int(40 * len(word) * 0.75) + 40, 90), 0)
            ImageDraw.Draw(tile).text((10, 15), word, fill=255, font=font(44))
            tile = tile.transform(tile.size, Image.Transform.AFFINE, (1, rnd.uniform(-0.35, -0.1), 0, 0, 1, 0))
            tile = tile.rotate(rnd.uniform(-5, 5), expand=False).filter(ImageFilter.MaxFilter(3))
            arr = np.asarray(tile, np.float32)
            shift = (np.sin(np.arange(arr.shape[1]) / rnd.uniform(9, 16)) * rnd.uniform(2, 4)).astype(int)
            arr = np.stack([np.roll(arr[:, c], shift[c]) for c in range(arr.shape[1])], axis=1)
            mask = Image.fromarray(arr.astype(np.uint8))
            img.paste(Image.new("L", mask.size, 30), (x, y + rnd.randint(-6, 6)), mask)
            x += mask.width - 20 + rnd.randint(5, 25)
    return img.convert("RGB")


def ocr_stop() -> dict:
    from vault.ingest.extract_pdf import stops_early
    from vault.ingest.ocr import OcrStats, ocr_image

    cfg = Config()
    engine = models.ocr_engine(cfg)
    pages = {}
    for i in range(3):
        pages[f"dense_{i}"] = ("printed", _printed(10 + i))
    for i in range(2):
        pages[f"small_{i}"] = ("printed", _printed(20 + i, size=20))
    pages["title"] = ("printed", _title(30))
    pages["contents"] = ("printed", _contents(31))
    pages["noisy_0"], pages["noisy_1"] = ("printed", _noisy(40)), ("printed", _noisy(41))
    pages["figure"] = ("printed", _figure(50))
    pages["two_columns"] = ("printed", _printed(60, columns=2))
    pages["blank"] = ("blank", _printed(70, lines=0))
    for i in range(5):
        pages[f"scribble_{i}"] = ("handwriting", _scribble(80 + i, lined=i % 2 == 0))
    for i in range(3):
        pages[f"neat_{i}"] = ("neat", _neat_hand(90 + i))
    rows, stats = [], {}
    for name, (group, img) in pages.items():
        st = OcrStats()
        ocr_image(img, engine, cfg, stats=st)
        stats[name] = st
        rows.append({"page": name, "group": group, "detected": st.detected, "kept": st.kept,
                     "mean_conf": round(st.mean_conf, 3)})
        print(f"{name:<12} {group:<11} detected {st.detected:>4} kept {st.kept:>4} mean conf {st.mean_conf:.3f}")

    printed = [n for n, (g, _) in pages.items() if g == "printed"]
    docs = {  # 3-page probes, as the rule sees a PDF's first OCR'd pages
        "book: title, contents, dense": ["title", "contents", "dense_0"],
        "book: blank, title, dense": ["blank", "title", "dense_1", "dense_2"],
        "scan: noisy x2, figure": ["noisy_0", "noisy_1", "figure"],
        "scan: small, two columns, small": ["small_0", "two_columns", "small_1"],
        "report: figure, title, contents": ["figure", "title", "contents"],
        "handwriting: scribbles": ["scribble_0", "scribble_1", "scribble_2"],
        "handwriting: blank, scribbles": ["blank", "scribble_3", "scribble_4", "scribble_0"],
        "handwriting: printed cover, scribbles": ["title", "scribble_1", "scribble_3"],
        "neat handwriting": ["neat_0", "neat_1", "neat_2"],
    }

    def mean_conf(names):  # what stops_early computes: the first 3 pages with ink
        inked = [stats[n] for n in names if stats[n].detected][:3]
        return round(sum(s.conf_sum for s in inked) / sum(s.detected for s in inked), 3)

    measured = {d: mean_conf(n) for d, n in docs.items()}
    should_continue = [d for d in docs if not d.startswith(("handwriting", "neat"))]
    should_stop = [d for d in docs if d.startswith("handwriting")]
    lo, hi = min(measured[d] for d in should_continue), max(measured[d] for d in should_stop)
    decisions = {d: stops_early([stats[n] for n in names], cfg) for d, names in docs.items()}
    for d in docs:
        print(f"{d:<38} mean conf {measured[d]:.3f} -> {'STOP' if decisions[d] else 'continue'}")
    return {"summary": {
        "mean_conf": {"printed_min": lo, "handwriting_max": hi, "midpoint": round((lo + hi) / 2, 3)},
        "config": {"pdf_ocr_stop_conf": cfg.pdf_ocr_stop_conf},
        "decisions": decisions, "printed_pages": len(printed),
    }, "rows": rows, "docs": measured}


def main(argv: list[str]) -> None:
    jobs = {"dense": dense, "tiny": tiny, "prose": prose, "toc": toc, "clip": clip, "rule": rule, "kind": kind, "ocr-stop": ocr_stop}
    if not argv or argv[0] not in jobs:
        sys.exit(f"usage: python bench/calibrate.py {{{'|'.join(jobs)}}}")
    out = jobs[argv[0]]()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"calibrate_{argv[0]}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out["summary"], indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
