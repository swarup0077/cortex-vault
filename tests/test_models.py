import numpy as np
import pytest

from vault import models
from vault.config import Config


def test_set_text_embedder_overrides(fake_embedder) -> None:
    assert models.text_embedder() is fake_embedder


@pytest.mark.slow
def test_bge_shapes_and_norms() -> None:
    e = models.FastembedTextEmbedder(Config())
    docs = e.embed_documents(["the cat sat on the mat", "quarterly electricity bill"])
    q = e.embed_query("power invoice")
    assert docs.shape == (2, 384) and docs.dtype == np.float32
    assert np.allclose(np.linalg.norm(docs, axis=1), 1, atol=1e-5)
    assert abs(np.linalg.norm(q) - 1) < 1e-5
    assert docs[1] @ q > docs[0] @ q


def test_ocr_engine_is_lazy_and_injectable() -> None:
    class Fake:
        name = "fake-ocr"

        def read(self, image, *, use_cls=None):
            return []

    fake = Fake()
    models.set_ocr_engine(fake)
    try:
        assert models.ocr_engine() is fake
    finally:
        models.set_ocr_engine(None)


def test_ocr_models_ship_with_the_wheel() -> None:
    """The configured OCR files are bundled, so the engine never needs a download."""
    from pathlib import Path

    import rapidocr

    bundled = Path(rapidocr.__file__).parent / "models"
    cfg = Config()
    for name in (cfg.ocr_det_model, cfg.ocr_rec_model, cfg.ocr_cls_model):
        assert (bundled / name).is_file(), name


@pytest.mark.slow
def test_rapidocr_reads_a_drawn_line() -> None:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (700, 120), "white")
    ImageDraw.Draw(img).text((20, 30), "Settings saved", fill="black", font=ImageFont.load_default(size=40))
    lines = models.RapidOcrEngine(Config()).read(np.asarray(img))
    assert [l.text for l in lines] == ["Settings saved"]
    ln = lines[0]
    assert 0 <= ln.x0 < 60 and 15 <= ln.y0 < 50 and ln.x1 > 250 and ln.conf > 0.9


@pytest.mark.parametrize("cpus, threads", [(16, 8), (8, 8), (4, 4), (2, 2), (None, 1)])
def test_ocr_threads_default_is_capped_by_cpu_count(monkeypatch, cpus, threads) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: cpus)
    assert Config().ocr_threads == threads


def test_ocr_threads_reach_onnx_runtime(monkeypatch) -> None:
    """The engine passes the thread count to RapidOCR's ONNX Runtime session (no model is loaded here)."""
    import rapidocr

    seen = {}

    class FakeRapidOCR:
        def __init__(self, params):
            seen.update(params)

    monkeypatch.setattr(rapidocr, "RapidOCR", FakeRapidOCR)
    models.RapidOcrEngine(Config(ocr_threads=3))
    assert seen["EngineConfig.onnxruntime.intra_op_num_threads"] == 3
    models.RapidOcrEngine(Config(ocr_threads=0))
    assert seen["EngineConfig.onnxruntime.intra_op_num_threads"] == -1  # 0 → ONNX Runtime's own default
