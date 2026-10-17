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
