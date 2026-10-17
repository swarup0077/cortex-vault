"""Generated pictures for the CLIP calibrations in `calibrate.py` (deterministic, Pillow only).

`SCENES` are simple drawn scenes that the real CLIP recognises, each with the query that should find it. The
other helpers draw text-free "photos" (noisy gradients and blobs) and screen captures (desktop UI, phone chat,
a code editor), for the gate's distractors and the screenshot-vs-photo check.
"""

from __future__ import annotations

import random
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

from bench_ocr import font, screenshot, sentence  # noqa: E402

W, H = 640, 480


def _sky(d: ImageDraw.ImageDraw, top: tuple, bottom: tuple, h: int) -> None:
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))


def sunset_beach() -> Image.Image:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    _sky(d, (120, 40, 120), (250, 130, 30), int(H * 0.55))
    d.ellipse((W * 0.42, H * 0.40, W * 0.58, H * 0.62), fill=(255, 200, 60))
    d.rectangle((0, H * 0.55, W, H * 0.78), fill=(30, 50, 110))
    d.rectangle((0, H * 0.78, W, H), fill=(220, 190, 140))
    return img


def checkerboard() -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    for i in range(8):
        for j in range(8):
            if (i + j) % 2:
                d.rectangle((i * W / 8, j * H / 8, (i + 1) * W / 8, (j + 1) * H / 8), fill="black")
    return img


def green_field() -> Image.Image:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    _sky(d, (70, 130, 220), (170, 210, 245), int(H * 0.5))
    d.rectangle((0, H * 0.5, W, H), fill=(60, 150, 50))
    rnd = random.Random(1)
    for _ in range(400):  # grass blades
        x, y = rnd.uniform(0, W), rnd.uniform(H * 0.5, H)
        d.line([(x, y), (x + rnd.uniform(-3, 3), y - rnd.uniform(4, 12))], fill=(40, 120, 35), width=1)
    return img


def snowy_mountains() -> Image.Image:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    _sky(d, (60, 120, 210), (190, 220, 250), H)
    for x0, peak, x1 in [(-60, 120, 300), (180, 70, 520), (380, 150, 720)]:
        px = (x0 + x1) / 2
        d.polygon([(x0, H * 0.8), (px, peak), (x1, H * 0.8)], fill=(110, 110, 125))
        d.polygon([(px - (px - x0) * 0.3, peak + (H * 0.8 - peak) * 0.3), (px, peak),
                   (px + (x1 - px) * 0.3, peak + (H * 0.8 - peak) * 0.3)], fill=(250, 250, 255))
    d.rectangle((0, H * 0.8, W, H), fill=(235, 240, 248))
    return img


def night_moon() -> Image.Image:
    img = Image.new("RGB", (W, H), (10, 15, 40))
    d = ImageDraw.Draw(img)
    rnd = random.Random(2)
    for _ in range(150):
        x, y = rnd.uniform(0, W), rnd.uniform(0, H)
        d.point((x, y), fill=(255, 255, 230))
    d.ellipse((380, 60, 520, 200), fill=(245, 245, 225))
    for cx, cy, r in [(420, 110, 14), (470, 150, 10), (440, 170, 7)]:
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(215, 215, 200))
    return img


def dice() -> Image.Image:
    img = Image.new("RGB", (W, H), (120, 80, 45))  # a wooden table
    d = ImageDraw.Draw(img)
    for y in range(0, H, 9):
        d.line([(0, y), (W, y + 4)], fill=(105, 70, 40), width=2)
    for x0, y0, pips in [(150, 160, [(0.5, 0.5)]), (360, 200, [(0.25, 0.25), (0.75, 0.75), (0.5, 0.5),
                                                                (0.25, 0.75), (0.75, 0.25)])]:
        d.rounded_rectangle((x0, y0, x0 + 130, y0 + 130), 18, fill=(250, 250, 250), outline=(60, 60, 60), width=2)
        for fx, fy in pips:
            cx, cy = x0 + 130 * fx, y0 + 130 * fy
            d.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), fill=(20, 20, 20))
    return img


def traffic_light() -> Image.Image:
    img = Image.new("RGB", (W, H), (150, 190, 230))
    d = ImageDraw.Draw(img)
    d.rectangle((310, 300, 330, H), fill=(60, 60, 60))
    d.rounded_rectangle((250, 40, 390, 320), 20, fill=(25, 25, 25))
    for i, colour in enumerate([(230, 30, 30), (240, 190, 30), (40, 200, 70)]):
        d.ellipse((280, 60 + i * 85, 360, 140 + i * 85), fill=colour)
    return img


def red_apple() -> Image.Image:
    img = Image.new("RGB", (W, H), (245, 240, 230))
    d = ImageDraw.Draw(img)
    d.ellipse((200, 130, 440, 380), fill=(200, 25, 30))
    d.ellipse((250, 170, 300, 220), fill=(235, 90, 90))  # highlight
    d.line([(320, 140), (330, 90)], fill=(90, 60, 30), width=8)
    d.ellipse((330, 85, 400, 120), fill=(60, 150, 50))
    return img


def house() -> Image.Image:
    img = Image.new("RGB", (W, H), (160, 205, 240))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 380, W, H), fill=(80, 160, 60))
    d.rectangle((200, 220, 440, 390), fill=(235, 220, 190))
    d.polygon([(180, 225), (320, 110), (460, 225)], fill=(180, 40, 30))
    d.rectangle((295, 300, 345, 390), fill=(110, 70, 40))
    for x in (225, 375):
        d.rectangle((x, 250, x + 45, 290), fill=(200, 230, 250), outline="white", width=3)
    return img


def rainbow() -> Image.Image:
    img = Image.new("RGB", (W, H), (120, 180, 235))
    d = ImageDraw.Draw(img)
    colours = [(230, 40, 40), (245, 140, 30), (245, 225, 40), (60, 180, 70), (40, 90, 220), (120, 50, 170)]
    for i, c in enumerate(colours):
        r = 300 - i * 22
        d.arc((W / 2 - r, H - r, W / 2 + r, H + r), 180, 360, fill=c, width=22)
    return img


def bar_chart() -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.line([(60, 420), (600, 420)], fill="black", width=3)
    d.line([(60, 420), (60, 40)], fill="black", width=3)
    for i, h in enumerate([120, 260, 190, 330, 90, 220]):
        d.rectangle((90 + i * 85, 420 - h, 150 + i * 85, 420), fill=(60, 110, 200))
    return img


def smiley() -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((170, 90, 470, 390), fill=(255, 215, 0), outline=(40, 40, 40), width=4)
    d.ellipse((250, 180, 285, 230), fill=(40, 40, 40))
    d.ellipse((355, 180, 390, 230), fill=(40, 40, 40))
    d.arc((240, 220, 400, 340), 20, 160, fill=(40, 40, 40), width=8)
    return img


SCENES: list[tuple[str, Callable[[], Image.Image], str]] = [  # (name, drawer, the query that should find it)
    ("sunset", sunset_beach, "sunset at the beach"),
    ("chess", checkerboard, "a chessboard"),
    ("field", green_field, "a green field under a blue sky"),
    ("mountains", snowy_mountains, "snowy mountains"),
    ("moon", night_moon, "the moon in the night sky"),
    ("dice", dice, "dice on a wooden table"),
    ("traffic", traffic_light, "a traffic light"),
    ("apple", red_apple, "a red apple"),
    ("house", house, "a house with a red roof"),
    ("rainbow", rainbow, "a rainbow in the sky"),
    ("chart", bar_chart, "a bar chart"),
    ("smiley", smiley, "a yellow smiley face"),
]


def noise_photo(seed: int, size: tuple[int, int] = (W, H)) -> Image.Image:
    """A text-free, photo-like picture: a colour gradient with soft blobs and sensor noise."""
    rnd = random.Random(seed)
    w, h = size
    top, bottom = [tuple(rnd.randrange(256) for _ in range(3)) for _ in range(2)]
    grad = Image.linear_gradient("L").resize((w, h))
    img = Image.composite(Image.new("RGB", (w, h), top), Image.new("RGB", (w, h), bottom), grad)
    d = ImageDraw.Draw(img)
    for _ in range(8):
        x, y, r = rnd.randrange(w), rnd.randrange(h), rnd.randrange(w // 20, w // 4)
        d.ellipse((x - r, y - r, x + r, y + r), fill=tuple(rnd.randrange(256) for _ in range(3)))
    arr = np.asarray(img.filter(ImageFilter.GaussianBlur(w / 160)), np.float32)
    arr += np.random.default_rng(seed).normal(0, 8, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def desktop_screenshot(seed: int) -> Image.Image:
    return screenshot(seed)[0]


def phone_chat(seed: int, size: tuple[int, int] = (720, 1600)) -> Image.Image:
    """A phone chat screen: status bar, header, alternating message bubbles, input box."""
    rnd = random.Random(seed)
    w, h = size
    img = Image.new("RGB", (w, h), (236, 229, 221))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w, 50), fill=(7, 94, 84))
    d.text((20, 12), "09:41", fill="white", font=font(24))
    d.rectangle((0, 50, w, 150), fill=(7, 94, 84))
    d.text((110, 80), sentence(rnd, 2), fill="white", font=font(34))
    y = 180
    while y < h - 200:
        left = rnd.random() < 0.5
        bw = rnd.randint(int(w * 0.45), int(w * 0.75))
        x0 = 24 if left else w - 24 - bw
        d.rounded_rectangle((x0, y, x0 + bw, y + 110), 18, fill="white" if left else (220, 248, 198))
        d.text((x0 + 18, y + 16), sentence(rnd, 4), fill="black", font=font(26))
        d.text((x0 + 18, y + 56), sentence(rnd, 3), fill="black", font=font(26))
        y += 140
    d.rounded_rectangle((20, h - 110, w - 120, h - 30), 40, fill="white")
    d.ellipse((w - 100, h - 110, w - 20, h - 30), fill=(7, 94, 84))
    return img


def code_editor(seed: int, size: tuple[int, int] = (1600, 900)) -> Image.Image:
    """A dark-theme code editor: tab bar, line numbers, syntax-coloured lines."""
    rnd = random.Random(seed)
    w, h = size
    img = Image.new("RGB", (w, h), (30, 30, 30))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w, 36), fill=(45, 45, 45))
    d.text((20, 9), "main.py", fill=(220, 220, 220), font=font(16))
    colours = [(86, 156, 214), (206, 145, 120), (220, 220, 170), (156, 220, 254), (106, 153, 85)]
    for i in range((h - 50) // 24):
        y = 50 + i * 24
        d.text((12, y), f"{i + 1:>3}", fill=(110, 110, 110), font=font(16))
        x = 70 + 32 * rnd.randint(0, 3)
        for _ in range(rnd.randint(1, 5)):
            word = rnd.choice(["def", "return", "self", "import", "for", "in", "if", "None", "print", "data",
                               "result", "config", "items", "value", "=", "(", ")", ":"])
            d.text((x, y), word, fill=rnd.choice(colours), font=font(16))
            x += 12 * len(word) + 10
    return img
