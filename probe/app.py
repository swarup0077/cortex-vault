"""M0 deploy probe: a hello-world Gradio app that embeds one query with bge-small via fastembed.

Checks that the CPU/ONNX stack runs on the hosting target (SPEC §15). Delete after M4.
"""

import os
import platform
import sys
import time
from importlib.metadata import version

import gradio as gr
import numpy as np
import onnxruntime as ort
from fastembed import TextEmbedding

try:
    import spaces

    gpu = spaces.GPU
except ImportError:  # not on HF Spaces: no-op decorator

    def gpu(fn):
        return fn


MODEL_NAME = "BAAI/bge-small-en-v1.5"

_t0 = time.perf_counter()
model = TextEmbedding(MODEL_NAME)
LOAD_MS = (time.perf_counter() - _t0) * 1000


def env_info() -> str:
    lines = [
        f"python: {sys.version.split()[0]}",
        f"platform: {platform.platform()}",
        f"cpu_count: {os.cpu_count()}",
        f"onnxruntime providers: {ort.get_available_providers()}",
        f"model load ms: {LOAD_MS:.0f}",
    ]
    lines += [f"{pkg}: {version(pkg)}" for pkg in ("fastembed", "onnxruntime", "gradio", "numpy")]
    return "\n".join(lines)


def embed(query: str) -> dict:
    """Embed one query on CPU. Runs outside @spaces.GPU, so it never queues for a GPU or uses quota."""
    t0 = time.perf_counter()
    vec = np.asarray(next(iter(model.query_embed([query or "hello world"]))), dtype=np.float32)
    encode_ms = (time.perf_counter() - t0) * 1000
    return {
        "model": MODEL_NAME,
        "dim": int(vec.shape[0]),
        "l2_norm": round(float(np.linalg.norm(vec)), 4),
        "first5": [round(float(x), 4) for x in vec[:5]],
        "encode_ms": round(encode_ms, 1),
    }


@gpu
def gpu_ping() -> str:
    """Exists only so ZeroGPU's startup check finds a @spaces.GPU function."""
    return "ok"


with gr.Blocks(title="Search Vault probe") as demo:
    gr.Markdown("# Personal Search Vault — deploy probe\nEmbeds one query with bge-small (fastembed, ONNX, CPU).")
    query = gr.Textbox(label="Query", value="screenshot of the access denied error")
    embed_btn = gr.Button("Embed", variant="primary")
    result = gr.JSON(label="Embedding")
    embed_btn.click(embed, inputs=query, outputs=result, api_name="embed")
    with gr.Row():
        ping_btn = gr.Button("GPU ping")
        ping_out = gr.Textbox(label="GPU ping", interactive=False)
    ping_btn.click(gpu_ping, outputs=ping_out, api_name="gpu_ping")
    gr.Textbox(label="Environment", value=env_info(), lines=9, interactive=False)

if __name__ == "__main__":
    demo.launch()
