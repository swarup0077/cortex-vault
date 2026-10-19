"""deploy/check.py, the runbook's health check (deploy/azure.md §8, open item ab): it sends a search the way the
page does, through Gradio's queue with the query as event data, against the app served in-process over generated
files."""

import importlib.util
import socket
import threading
import time
from pathlib import Path

import pytest

from vault.config import Config
from vault.library import Library

CHECK = Path(__file__).resolve().parents[1] / "deploy" / "check.py"
pytestmark = [pytest.mark.filterwarnings("ignore:'future.no_silent_downcasting' is deprecated"),  # Gradio's queue
              pytest.mark.filterwarnings("ignore:The copy keyword is deprecated")]


def load_check():
    spec = importlib.util.spec_from_file_location("deploy_check", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def served(tmp_path, fake_embedder, make_search_corpus):
    import uvicorn

    import app
    from vault.ui.layout import UiContext, build_blocks
    from vault.ui.server import make_app

    lib = Library(Config(data_dir=tmp_path / "data"))
    lib.index([make_search_corpus(tmp_path / "files")])
    ctx = UiContext("local", lib)
    with socket.socket() as s:
        s.bind((app.HOST, 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(app.server_config(make_app(build_blocks(ctx), ctx.resolve, demo=False), port))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    yield f"http://{app.HOST}:{port}/"
    server.should_exit = True
    thread.join(15)


def test_the_health_check_searches_as_the_page_does(served, capsys) -> None:
    check = load_check()
    names, secs = check.search(served, "singular value decomposition")
    assert names[0] == "linear algebra notes.pdf" and len(names) >= 1 and secs < 30
    assert check.main([served, "singular value decomposition", "linear algebra notes.pdf"]) == 0
    assert capsys.readouterr().out.startswith('OK "singular value decomposition": #1 linear algebra notes.pdf')
    assert check.main([served, "singular value decomposition", "recipe.txt"]) == 1
    assert capsys.readouterr().out.startswith("FAIL")
    assert check.main([served]) == 2  # usage
