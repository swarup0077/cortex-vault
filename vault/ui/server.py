"""The web server: Gradio mounted on a FastAPI app that also serves thumbnails and previews by file id only
(SPEC §8, §18). No route takes a path; a session's files need its id, which the page only ever gets for its own
session.
"""

from __future__ import annotations

import io
from collections.abc import Callable

import gradio as gr
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from vault.library import Library
from vault.ui.layout import STYLES, theme

Resolve = Callable[[str | None], Library | None]  # session id (None: the main index) → its library

HEAD = '<meta name="robots" content="noindex">'


def make_app(blocks: gr.Blocks, resolve: Resolve, *, max_file_size: str | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def library(session: str | None) -> Library:
        lib = resolve(session)
        if lib is None:
            raise HTTPException(404)
        return lib

    @app.get("/thumb/{file_id}")
    def thumb(file_id: int, s: str | None = None) -> FileResponse:
        path = library(s).thumb(file_id)
        if path is None:
            raise HTTPException(404)
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/preview/{file_id}")
    def preview(file_id: int, page: int | None = None, s: str | None = None) -> Response:
        img = library(s).preview(file_id, page)
        if img is None:
            raise HTTPException(404)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "WEBP", quality=82, method=4)
        return Response(buf.getvalue(), media_type="image/webp", headers={"Cache-Control": "private, max-age=600"})

    return gr.mount_gradio_app(app, blocks, path="", footer_links=[], max_file_size=max_file_size, theme=theme(),
                               css=STYLES, head=HEAD, allowed_paths=None)
