"""Kind classifier (SPEC §6.2): one test per rule, on images generated with Pillow."""

from pathlib import Path

import pytest
from PIL import Image

import numpy as np

from conftest import camera_exif, save_image
from vault.ingest.classify import classify, clip_kind, is_final, known_screen_capture


def img(tmp_path: Path, name: str, size=(640, 480), **save) -> Path:
    return save_image(Image.new("RGB", size, "gray"), tmp_path / name, **save)


@pytest.mark.parametrize("name, label", [
    ("Screenshot 2026-10-16 170612.png", "snipping-tool"),
    ("Screenshot (12).png", "prtscn"),
    ("Screenshot_20251012-101530_Chrome.jpg", "android"),
    ("Screenshot_20251012_101530_Settings.jpg", "android"),  # Samsung
    ("Screen Shot 2025-03-12 at 10.15.30.png", "macos"),
    ("Screenshot 2025-03-12 at 10.15.30.png", "macos"),
    ("scr_0042.jpg", "generic"),
    ("screencapture-github-com-2025.png", "generic"),
])
def test_rule1_screenshot_filenames(tmp_path, name, label) -> None:
    # A screenshot name wins even over camera EXIF (rule 1 comes first).
    assert classify(img(tmp_path, name, exif=camera_exif())) == ("screenshot", f"filename:{label}")


@pytest.mark.parametrize("name", ["IMG_2041.jpg", "IMG_2041.png"])
def test_rule2_camera_exif_is_a_photo(tmp_path, name) -> None:
    assert classify(img(tmp_path, name, exif=camera_exif())) == ("photo", "exif:camera")


def test_rule2_model_alone_is_enough(tmp_path) -> None:
    assert classify(img(tmp_path, "a.jpg", exif=camera_exif(make=None))) == ("photo", "exif:camera")


def test_rule3_png_without_camera_exif(tmp_path) -> None:
    assert classify(img(tmp_path, "diagram.png", size=(1234, 777))) == ("screenshot", "png-no-exif")


@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920), (1366, 768), (1170, 2532)])
def test_rule4_screen_size_without_exif(tmp_path, size) -> None:
    assert classify(img(tmp_path, "forwarded.jpg", size=size)) == ("screenshot", f"screen-size:{size[0]}x{size[1]}")


def test_rule4_needs_no_exif_at_all(tmp_path) -> None:
    software_only = Image.Exif()
    software_only[0x0131] = "Photo editor 2.1"
    assert classify(img(tmp_path, "edited.jpg", size=(1920, 1080), exif=software_only)) == ("photo", "default")


@pytest.mark.parametrize("size", [(1080, 5000), (720, 1440), (1440, 6000)])
def test_rule5_tall_phone_width(tmp_path, size) -> None:
    assert classify(img(tmp_path, "long.jpg", size=size)) == ("screenshot", f"tall-phone:{size[0]}x{size[1]}")


def test_rule5_camera_exif_wins_for_tall_photos(tmp_path) -> None:
    """Some phones shoot 20:9 photos: 1080×2400 with camera EXIF stays a photo."""
    path = img(tmp_path, "PXL_20250312_101530123.jpg", size=(1080, 2400), exif=camera_exif())
    assert classify(path) == ("photo", "exif:camera")


def test_rule5_upright_size_counts(tmp_path) -> None:
    sideways = Image.Exif()
    sideways[0x0112] = 6  # stored 5000×1080, shown 1080×5000
    assert classify(img(tmp_path, "long.jpg", size=(5000, 1080), exif=sideways)) == ("screenshot", "tall-phone:1080x5000")


@pytest.mark.parametrize("size", [(1234, 987), (1080, 2000), (1000, 3000)])  # no screen size; too short; no phone width
def test_rule6_otherwise_photo(tmp_path, size) -> None:
    assert classify(img(tmp_path, "pic.jpg", size=size)) == ("photo", "default")


@pytest.mark.parametrize("name, kind", [("broken.jpg", "photo"), ("broken.png", "screenshot"),
                                        ("Screenshot (3).jpg", "screenshot")])
def test_unreadable_image_falls_back_to_name_and_extension(tmp_path, name, kind) -> None:
    (tmp_path / name).write_bytes(b"not an image")
    assert classify(tmp_path / name)[0] == kind


# ---------------------------------------------------------------- CLIP decides what no rule does (rule 4, SPEC §6.2)


@pytest.mark.parametrize("reason, final, capture", [
    ("filename:snipping-tool", True, True), ("png-no-exif", True, True), ("exif:camera", True, False),
    ("clip:screenshot", True, False), ("clip:photo", True, False), ("ext:.pdf", True, False),
    ("screen-size:1600x900", False, False), ("tall-phone:720x1600", False, False), ("default", False, False),
    ("interim:ext", False, False),
])
def test_which_reasons_are_final_and_which_skip_the_angle_classifier(reason, final, capture) -> None:
    """Size rules are only the fallback until CLIP runs; only a name or PNG proves a screen capture."""
    assert is_final(reason) is final and known_screen_capture(reason) is capture


def test_clip_kind_compares_the_two_prompts() -> None:
    s, p = np.array([[1.0, 0.0]]), np.array([[0.0, 1.0]])
    assert clip_kind(np.array([0.8, 0.6]), s, p, 0.2) == ("screenshot", "clip:screenshot")  # leads by 0.2: inclusive
    assert clip_kind(np.array([0.6, 0.8]), s, p, 0.0) == ("photo", "clip:photo")
    assert clip_kind(np.array([0.8, 0.6]), s, p, 0.21) == ("photo", "clip:photo")
