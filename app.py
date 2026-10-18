"""Gradio entry point (SPEC §2): `VAULT_MODE=local` (default) searches your own index, `VAULT_MODE=demo` the
read-only demo index plus per-session uploads. Both bind 127.0.0.1; on the server Caddy is the public listener.

    python app.py                       # local: http://127.0.0.1:7860
    VAULT_MODE=demo python app.py       # demo
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from pathlib import Path

import uvicorn
from gradio.utils import get_upload_folder

from vault import models
from vault.config import REPO_ROOT, Config
from vault.library import Library
from vault.session import DEFAULT_ROOT, SessionManager
from vault.ui.layout import DEMO_EXAMPLES, LOCAL_EXAMPLES, UiContext, build_blocks
from vault.ui.server import make_app

log = logging.getLogger("vault.app")

HOST = "127.0.0.1"  # never another interface: local mode is private, demo mode sits behind Caddy (SPEC §2, §18)


def build_context(mode: str) -> UiContext:
    if mode == "demo":
        cfg = Config(data_dir=REPO_ROOT / "demo_index")
        library = Library(cfg, read_only=True, base_dir=REPO_ROOT)
        sessions = SessionManager(library, cfg, Path(os.environ.get("VAULT_SESSIONS_DIR") or DEFAULT_ROOT))
        return UiContext("demo", library, examples=DEMO_EXAMPLES, sessions=sessions,
                         upload_dir=Path(get_upload_folder()))
    if mode != "local":
        raise SystemExit(f"VAULT_MODE must be 'local' or 'demo', not {mode!r}")
    return UiContext("local", Library(Config()), examples=LOCAL_EXAMPLES)


def start_demo(ctx: UiContext) -> None:
    """Nothing uploaded in an earlier run survives a restart (SPEC §8): wipe every session and, when the service
    gives Gradio a folder of its own (GRADIO_TEMP_DIR, deploy/vault.service), Gradio's copies of uploads too."""
    ctx.sessions.wipe_all()
    if temp := os.environ.get("GRADIO_TEMP_DIR"):
        shutil.rmtree(temp, ignore_errors=True)
    ctx.sessions.start_reaper()


def warm_up(ctx: UiContext) -> None:
    """Load the query models and vectors before the first visitor does (SPEC §7.8)."""
    try:
        ctx.library.search("warm up")
        if ctx.demo:  # uploads need OCR and CLIP-vision; load them now rather than on the first upload
            models.ocr_engine(ctx.library.cfg)
            models.clip_vision(ctx.library.cfg)
        log.info("models warm")
    except Exception as e:  # e.g. an index built with other models: the first search shows the message
        log.warning("warm-up failed: %s", e)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per HF Hub request otherwise
    mode = os.environ.get("VAULT_MODE", "local").strip().lower()
    ctx = build_context(mode)
    if ctx.demo:
        start_demo(ctx)
    blocks = build_blocks(ctx)
    app = make_app(blocks, ctx.resolve, max_file_size="25mb" if ctx.demo else None)
    threading.Thread(target=warm_up, args=(ctx,), daemon=True).start()
    port = int(os.environ.get("GRADIO_SERVER_PORT", "7860"))
    log.info("Personal Search Vault (%s mode) on http://%s:%d", mode, HOST, port)
    uvicorn.run(app, host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()
