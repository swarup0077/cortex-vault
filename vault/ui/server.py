"""The web server: Gradio mounted on a FastAPI app that also serves files by id only (SPEC §8, §18).

`/thumb/{id}`, `/preview/{id}`, `/detail/{id}` (highlight boxes and the passage text, as JSON) and, in demo mode,
`/file/{id}` (a download) look the file up in the index; a session's files also need its `s=<uuid4>`. Anything
that isn't a plain id, an unknown id and another session's file all get the same 404, so nothing reveals whether
a file exists. The app's own assets (stylesheet, script, fonts, logo) are static files under `/cv/`.
"""

from __future__ import annotations

import io
import mimetypes
from collections.abc import Callable
from pathlib import Path

import gradio as gr
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from vault.library import Library

STATIC = Path(__file__).parent / "static"

Resolve = Callable[[str | None], Library | None]  # session id (None: the main index) → its library

# Dark unless the URL asks for ?__theme=light (SPEC §8): Gradio reads __theme, so it's set before Gradio starts.
THEME_JS = ("(function(){var u=new URL(window.location.href);if(!u.searchParams.get('__theme')){"
            "u.searchParams.set('__theme','dark');window.history.replaceState(null,'',u);}})();")
HEAD = ('<meta name="robots" content="noindex">'
        '<link rel="icon" href="/cv/logo/favicon.ico" sizes="any">'
        '<link rel="icon" href="/cv/logo/cortex-vault-icon.svg" type="image/svg+xml">'
        '<link rel="apple-touch-icon" href="/cv/logo/apple-touch-icon.png">'
        '<link rel="stylesheet" href="/cv/cortex.css">'
        f"<script>{THEME_JS}</script>"
        '<script src="/cv/cortex.js"></script>')


def theme() -> gr.themes.Base:
    """Plain font names (no gr.themes.GoogleFont), so nothing loads from Google Fonts; Geist comes from /cv/fonts."""
    return gr.themes.Base(font=["Geist", "system-ui", "sans-serif"], font_mono=["Geist Mono", "monospace"]).set(
        body_background_fill="#F7F7F5", body_background_fill_dark="#0B0B0C",
        body_text_color="#17171A", body_text_color_dark="#EDEDEF",
        block_background_fill="#F7F7F5", block_background_fill_dark="#0B0B0C",
    )


def file_id(raw: str) -> int:
    """A plain decimal id, or 404: no paths, no signs, nothing else."""
    if not raw.isascii() or not raw.isdigit() or len(raw) > 12:
        raise HTTPException(404)
    return int(raw)


def make_app(blocks: gr.Blocks, resolve: Resolve, *, demo: bool = True, max_file_size: str | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def library(session: str | None) -> Library:
        lib = resolve(session)
        if lib is None:
            raise HTTPException(404)
        return lib

    @app.get("/thumb/{raw_id}")
    def thumb(raw_id: str, s: str | None = None) -> FileResponse:
        path = library(s).thumb(file_id(raw_id))
        if path is None:
            raise HTTPException(404)
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/preview/{raw_id}")
    def preview(raw_id: str, page: int | None = None, s: str | None = None) -> Response:
        img = library(s).preview(file_id(raw_id), page)
        if img is None:
            raise HTTPException(404)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "WEBP", quality=82, method=4)
        return Response(buf.getvalue(), media_type="image/webp", headers={"Cache-Control": "private, max-age=600"})

    @app.get("/detail/{raw_id}")
    def detail(raw_id: str, chunk: int | None = None, page: int | None = None, q: str = "",
               s: str | None = None) -> JSONResponse:
        data = library(s).preview_data(file_id(raw_id), chunk, page, q[:200])
        if data is None:
            raise HTTPException(404)
        return JSONResponse(data, headers={"Cache-Control": "private, no-store"})

    @app.get("/file/{raw_id}")
    def download(raw_id: str, s: str | None = None) -> FileResponse:
        if not demo:  # local mode opens files with vault/platform.py instead
            raise HTTPException(404)
        found = library(s).download(file_id(raw_id))
        if found is None:
            raise HTTPException(404)
        path, name = found
        media = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media, filename=name, content_disposition_type="attachment",
                            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:  # Gradio's favicon_path only works when it isn't mounted at the root
        return FileResponse(STATIC / "logo" / "favicon.ico", media_type="image/x-icon")

    app.mount("/cv", StaticFiles(directory=STATIC), name="cv")
    return gr.mount_gradio_app(app, blocks, path="", footer_links=[], max_file_size=max_file_size, theme=theme(),
                               head=HEAD, allowed_paths=None)
