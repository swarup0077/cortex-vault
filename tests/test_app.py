"""app.py: no telemetry in either mode, and the context each mode builds (SPEC §2, §11, §18)."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_importing_the_app_turns_gradio_analytics_off() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("GRADIO_ANALYTICS_ENABLED", "HF_HUB_DISABLE_TELEMETRY")}
    code = ("import app, gradio.analytics as a, os; "
            "print(a.analytics_enabled(), os.environ['HF_HUB_DISABLE_TELEMETRY'])")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["False", "1"]


def test_demo_context_reads_the_committed_index_read_only() -> None:
    import app

    ctx = app.build_context("demo")
    assert ctx.demo and ctx.library.read_only and ctx.sessions is not None and ctx.upload_dir is not None
    assert ctx.sample_roots == ["corpus"]
