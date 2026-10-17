"""OCR speed/accuracy benchmark on generated 1920x1080 UI screenshots (SPEC §19, OCR benchmark).

    python bench/bench_ocr.py [key=value ...]

Keys are `Config` fields (e.g. ocr_det_limit_side=960 ocr_threads=4) plus n_images (8), rounds (3),
workers (1: N engines in parallel threads, reports throughput) and tag (a label). Values are read as JSON
when they parse (960, true), else as strings (max). Prints one JSON line. Run one process per config.

Images go through the same path the pipeline uses for screenshots: tiling, OCR, strip merge, digit fixes,
and the angle classifier per `ocr_cls_screenshots`.
"""

from __future__ import annotations

import json
import random
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the repo root, wherever it lives

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from vault.config import Config  # noqa: E402
from vault.ingest.ocr import ocr_image  # noqa: E402
from vault.models import RapidOcrEngine  # noqa: E402

WORDS = """time person year way day thing world life hand part child place case week company system program
question work government number night point home water room mother area money story fact month lot right study
book eye job word business issue side kind head house service friend father power hour game line end member law
car city community name president team minute idea body information back parent face others level office door
health art war history party result change morning reason research girl guy moment air teacher force education
settings account password network update download install server request response error warning message folder
file document report invoice payment order shipping delivery address customer support ticket status pending
approved rejected deadline meeting schedule calendar project budget review summary analysis dashboard chart""".split()


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)  # Pillow's bundled font: same pixels on every OS


def sentence(rnd: random.Random, n: int) -> str:
    return " ".join(rnd.choice(WORDS) for _ in range(n)).capitalize()


def screenshot(seed: int) -> tuple[Image.Image, list[str]]:
    """A 1920x1080 UI-like screenshot (~55 lines) and the strings drawn on it."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (1920, 1080), (243, 243, 243))
    d = ImageDraw.Draw(img)
    drawn: list[str] = []

    def text(xy, s, size, fill=(30, 30, 30)):
        d.text(xy, s, fill=fill, font=font(size))
        drawn.append(s)

    d.rectangle((0, 0, 1920, 40), fill=(32, 32, 32))
    text((16, 11), f"{sentence(rnd, 3)} - Workspace", 15, (240, 240, 240))
    d.rectangle((0, 40, 280, 1080), fill=(228, 228, 228))
    for i in range(14):
        text((24, 70 + i * 34), sentence(rnd, rnd.randint(1, 3)), 15)
    text((320, 70), sentence(rnd, 5), 26)
    y = 120
    for _ in range(4):  # paragraphs of 13–16 px body text
        size = rnd.choice([13, 14, 15, 16])
        for _ in range(rnd.randint(3, 6)):
            text((320, y), sentence(rnd, rnd.randint(12, 18)), size)
            y += int(size * 1.6)
        y += 18
    d.rectangle((1250, 640, 1860, 860), fill="white", outline=(160, 160, 160))  # an error dialog
    text((1270, 660), "Error", 18)
    text((1270, 700), f"Operation failed with code 0x{rnd.randrange(16**8):08X}.", 15)
    text((1270, 730), sentence(rnd, 7), 15)
    d.rectangle((1740, 810, 1840, 845), fill=(0, 95, 184))
    text((1775, 818), "OK", 15, (255, 255, 255))
    return img, drawn


def tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) >= 3]


def recall(drawn: list[str], lines) -> float:
    """Share of drawn words (≥ 3 chars) found in the OCR text."""
    want = [t for s in drawn for t in tokens(s)]
    got = {t for l in lines for t in tokens(l.text)}
    return sum(t in got for t in want) / len(want)


def parse_args(argv: list[str]) -> dict:
    out = {}
    for arg in argv:
        key, _, value = arg.partition("=")
        try:
            out[key] = json.loads(value)
        except json.JSONDecodeError:
            out[key] = value
    return out


def main(argv: list[str]) -> None:
    overrides = parse_args(argv)
    n_images = int(overrides.pop("n_images", 8))
    rounds = int(overrides.pop("rounds", 3))
    workers = int(overrides.pop("workers", 1))
    tag = overrides.pop("tag", "")
    cfg = replace(Config(), **overrides)
    use_cls = cfg.ocr_cls_screenshots
    shots = [screenshot(seed) for seed in range(n_images)]

    t0 = time.perf_counter()
    engines = [RapidOcrEngine(cfg) for _ in range(workers)]
    for e in engines:
        e.read(np.asarray(shots[0][0]), use_cls=use_cls)  # warm-up
    load_s = time.perf_counter() - t0

    per_round: list[float] = []
    splits: list[list[float]] = []
    recalls: list[float] = []
    if workers == 1:  # latency per image, with the detect/classify/recognise split
        eng = engines[0]
        for r in range(rounds):
            times = []
            for img, drawn in shots:
                t = time.perf_counter()
                lines = ocr_image(img, eng, cfg, use_cls=use_cls)
                times.append(time.perf_counter() - t)
                splits.append([x or 0.0 for x in eng.last_elapse])
                if r == 0:
                    recalls.append(recall(drawn, lines))
            per_round.append(statistics.median(times))
    else:  # throughput: N engines in parallel threads (ONNX Runtime releases the GIL); wall time / image
        with ThreadPoolExecutor(workers) as pool:
            for r in range(rounds):
                t = time.perf_counter()
                results = list(pool.map(lambda k: ocr_image(shots[k][0], engines[k % workers], cfg, use_cls=use_cls),
                                        range(len(shots))))
                per_round.append((time.perf_counter() - t) / len(shots))
                if r == 0:
                    recalls = [recall(drawn, lines) for (_, drawn), lines in zip(shots, results)]

    det, cls, rec = (statistics.median(s[i] for s in splits) if splits else 0.0 for i in range(3))
    print(json.dumps({
        "tag": tag, "config": {**overrides, "workers": workers},
        "ocr_threads": cfg.ocr_threads, "ms_per_image_median": round(1000 * statistics.median(per_round)),
        "round_medians_ms": [round(1000 * x) for x in per_round],
        "det_ms": round(1000 * det), "cls_ms": round(1000 * cls), "rec_ms": round(1000 * rec),
        "word_recall": round(statistics.mean(recalls), 4), "load_and_warmup_s": round(load_s, 2),
    }))


if __name__ == "__main__":
    main(sys.argv[1:])
