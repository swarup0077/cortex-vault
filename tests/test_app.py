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


def test_the_server_stops_within_its_grace_while_a_browser_holds_a_stream() -> None:
    """Open item aa: an open event stream (a browser tab's Gradio connection) kept a stopping server waiting until
    systemd killed it 90 s later. The server now gives open requests `SHUTDOWN_GRACE_S`, then closes them."""
    import asyncio
    import http.client
    import socket
    import threading
    import time

    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import StreamingResponse
    from starlette.routing import Route

    import app

    async def stream(request):
        async def events():
            while True:
                yield b"data: ping\n\n"
                await asyncio.sleep(0.1)
        return StreamingResponse(events(), media_type="text/event-stream")

    with socket.socket() as s:
        s.bind((app.HOST, 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(app.server_config(Starlette(routes=[Route("/events", stream)]), port))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    client = http.client.HTTPConnection(app.HOST, port, timeout=30)
    client.request("GET", "/events")
    assert client.getresponse().read(6) == b"data: "  # the stream is open
    t0 = time.monotonic()
    server.should_exit = True  # what SIGTERM does
    thread.join(app.SHUTDOWN_GRACE_S + 10)
    assert not thread.is_alive(), "the server waited for the open stream"
    assert time.monotonic() - t0 < app.SHUTDOWN_GRACE_S + 5
    client.close()
