"""The web server: Gradio mounted on a FastAPI app that also serves files by id only (SPEC §8, §18).

`/thumb/{id}`, `/preview/{id}`, `/detail/{id}` (highlight boxes and the passage text, as JSON) and, in demo mode,
`/file/{id}` (a download) look the file up in the index: the visitor's own upload session's copy when their
browser's cookie names a live one, else the main index. No URL names a session. Anything that isn't a plain id, an
unknown id and another session's file all get the same 404, so nothing reveals whether a file exists. In demo mode
`/session/claim` trades a new session's one-time ticket for its cookie, `/session/forget` clears a cookie that names
no live session, and `/session/uploads` lists the cookie's session's own uploads for Manage uploads (SPEC §8). The
app's own assets (stylesheet, script, logo) are static files under `/cv/`; the page names the stylesheet and the
script with a hash of their content (`asset`).
"""

from __future__ import annotations

import hashlib
import io
import mimetypes
import json
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import gradio as gr
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from vault.library import Library
from vault.session import COOKIE, SessionManager

STATIC = Path(__file__).parent / "static"

Resolve = Callable[[str | None], Library | None]  # the session cookie's token (or None) → the library to serve from
_CLAIM_MAX = 1024  # bytes in a /session/claim body

# Theme (SPEC §8): with no ?__theme=, Gradio follows the OS; a choice made with the header toggle is kept in this
# browser (cortex.js, `cv-theme`) and applied here, before Gradio reads __theme. ?__theme=light|dark wins.
THEME_JS = ("(function(){try{var u=new URL(window.location.href),t=u.searchParams.get('__theme'),s=null;"
            "if(t==='light'||t==='dark')return;try{s=window.localStorage.getItem('cv-theme');}catch(e){}"
            "if(s==='light'||s==='dark'){u.searchParams.set('__theme',s);window.history.replaceState(null,'',u);}"
            "}catch(e){}})();")


def asset(name: str, root: Path = STATIC) -> str:
    """`/cv/<name>?v=<hash of its content>`: the URL changes whenever the file does, so after a deploy a browser
    fetches the new stylesheet and script instead of running a copy it cached before (they're served without
    Cache-Control, so a browser may reuse one for a while). The page that names them has no validators, so a browser
    always fetches it again. Hashed when the app starts, which every deploy does (deploy/azure.md)."""
    digest = hashlib.sha256((root / name).read_bytes()).hexdigest()[:12]
    return f"/cv/{name}?v={digest}"


HEAD = ('<meta name="robots" content="noindex">'
        '<link rel="icon" href="/cv/logo/favicon.ico" sizes="any">'
        '<link rel="icon" href="/cv/logo/cortex-vault-icon.svg" type="image/svg+xml">'
        '<link rel="apple-touch-icon" href="/cv/logo/apple-touch-icon.png">'
        f'<link rel="stylesheet" href="{asset("cortex.css")}">'
        f"<script>{THEME_JS}</script>"
        f'<script src="{asset("cortex.js")}"></script>')


def theme() -> gr.themes.Base:
    """System fonts named as plain strings (no gr.themes.GoogleFont), so nothing loads from the internet."""
    return gr.themes.Base(font=["Arial", "Helvetica", "sans-serif"],
                          font_mono=["ui-monospace", "Consolas", "monospace"]).set(
        body_background_fill="#F4F0E8", body_background_fill_dark="#262521",
        body_text_color="#25221E", body_text_color_dark="#EEE8DC",
        block_background_fill="#F4F0E8", block_background_fill_dark="#262521",
    )


def file_id(raw: str) -> int:
    """A plain decimal id, or 404: no paths, no signs, nothing else."""
    if not raw.isascii() or not raw.isdigit() or len(raw) > 12:
        raise HTTPException(404)
    return int(raw)


def same_origin(request: Request) -> bool:
    """A request this site's own page sent with fetch: JSON (a cross-site form can't send that without a CORS
    preflight, which nothing here answers), and no other site in Sec-Fetch-Site or Origin."""
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return False
    if request.headers.get("sec-fetch-site", "same-origin") != "same-origin":
        return False
    origin = request.headers.get("origin")
    return origin is None or urlsplit(origin).netloc == request.headers.get("host")


def set_cookie(response: Response, token: str | None) -> None:
    """The session cookie, or (None) its removal: only the server reads it, only over HTTPS, never on a request from
    another site, for the whole app; gone when the browser closes (the session itself ends sooner, SPEC §8)."""
    if token is None:
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    else:
        response.set_cookie(COOKIE, token, path="/", secure=True, httponly=True, samesite="strict")


def make_app(blocks: gr.Blocks, resolve: Resolve, *, demo: bool = True,
             max_file_size: str | int | None = None, sessions: SessionManager | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def library(request: Request) -> Library:
        lib = resolve(request.cookies.get(COOKIE))
        if lib is None:
            raise HTTPException(404)
        return lib

    @app.get("/thumb/{raw_id}")
    def thumb(raw_id: str, request: Request) -> FileResponse:
        path = library(request).thumb(file_id(raw_id))
        if path is None:
            raise HTTPException(404)
        return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/preview/{raw_id}")
    def preview(raw_id: str, request: Request, page: int | None = None) -> Response:
        img = library(request).preview(file_id(raw_id), page)
        if img is None:
            raise HTTPException(404)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "WEBP", quality=82, method=4)
        return Response(buf.getvalue(), media_type="image/webp", headers={"Cache-Control": "private, max-age=600"})

    @app.get("/detail/{raw_id}")
    def detail(raw_id: str, request: Request, chunk: int | None = None, page: int | None = None,
               q: str = "") -> JSONResponse:
        data = library(request).preview_data(file_id(raw_id), chunk, page, q[:200])
        if data is None:
            raise HTTPException(404)
        return JSONResponse(data, headers={"Cache-Control": "private, no-store"})

    @app.get("/file/{raw_id}")
    def download(raw_id: str, request: Request) -> FileResponse:
        if not demo:  # local mode opens files with vault/platform.py instead
            raise HTTPException(404)
        found = library(request).download(file_id(raw_id))
        if found is None:
            raise HTTPException(404)
        path, name = found
        media = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media, filename=name, content_disposition_type="attachment",
                            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    if sessions is not None:  # demo mode: the upload session's cookie
        @app.post("/session/claim")
        async def claim(request: Request) -> Response:
            """A new session's page trades its one-time ticket for the session's cookie (the only way it's set)."""
            if not same_origin(request):
                raise HTTPException(403)
            body = await request.body()
            if len(body) > _CLAIM_MAX:
                raise HTTPException(413)
            try:
                ticket = json.loads(body or b"{}").get("ticket")
            except (ValueError, AttributeError):
                raise HTTPException(400) from None
            token = sessions.claim(ticket if isinstance(ticket, str) else None)
            if token is None:
                raise HTTPException(404)
            response = Response(status_code=204, headers={"Cache-Control": "no-store"})
            set_cookie(response, token)
            return response

        @app.get("/session/uploads")
        def uploads(request: Request) -> JSONResponse:
            """Manage uploads: the files this browser's session uploaded (by its cookie only), newest first."""
            files = sessions.uploads(sessions.by_token(request.cookies.get(COOKIE)))
            return JSONResponse({"files": files}, headers={"Cache-Control": "private, no-store"})

        @app.post("/session/forget")
        def forget(request: Request) -> Response:
            """Clear a cookie that names no live session (expired, deleted, or not ours); a live one is kept."""
            if not same_origin(request):
                raise HTTPException(403)
            response = Response(status_code=204, headers={"Cache-Control": "no-store"})
            if sessions.by_token(request.cookies.get(COOKIE)) is None:
                set_cookie(response, None)
            return response

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:  # Gradio's favicon_path only works when it isn't mounted at the root
        return FileResponse(STATIC / "logo" / "favicon.ico", media_type="image/x-icon")

    app.mount("/cv", StaticFiles(directory=STATIC), name="cv")
    return gr.mount_gradio_app(app, blocks, path="", footer_links=[], max_file_size=max_file_size, theme=theme(),
                               head=HEAD, allowed_paths=None)
