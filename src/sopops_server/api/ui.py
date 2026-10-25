"""The web UI: built assets, and index.html for every other path so the client-side router can take it.

A UI built for a sub-path (`/app/`, `sopops_server.urls`) is served under it only: a page asked for outside it, an old
bookmark of `/w/demo` say, is sent to the same page under it. A path under any other prefix (the API's `/api`, given
by a router in X-Forwarded-Prefix) that no route answered is the API's 404, not the UI: that is how a deployment that
serves the UI from elsewhere (sopops.dev serves it from Cloudflare) keeps the server's own copy out of the API."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from .. import urls

NO_UI = """<!doctype html><meta charset="utf-8"><title>sopops</title>
<body style="font:15px system-ui;max-width:60ch;margin:60px auto;padding:0 16px">
<h1>sopops</h1><p>The API is running (<a href="/docs">/docs</a>), but the web UI isn't built.
From a clone: <code>pnpm -C web install &amp;&amp; pnpm -C web build</code>, then restart. Or use the Docker image.</p>"""


def web_dir() -> Path | None:
    """The built UI (web/dist): SOPOPS_WEB_DIR, else bundled in the package (wheels and Docker), else a clone's web/dist."""
    here = Path(__file__).resolve().parent.parent
    candidates = [os.environ.get("SOPOPS_WEB_DIR"), here / "static", here.parents[1] / "web" / "dist"]
    for c in candidates:
        if c and (Path(c) / "index.html").is_file():
            return Path(c)
    return None


def router(ui: Path | None = None) -> APIRouter:
    """Mounted last: it answers every path the API doesn't."""
    r = APIRouter()
    ui = ui or web_dir()

    @r.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request) -> Response:
        layout, under = urls.layout(request), urls.root_prefix(request)
        # Under a prefix that isn't the UI's (the API's /api, say), a path no route answered is the API's 404.
        if path == "v1" or path.startswith("v1/") or (under and under != layout.ui):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        if layout.ui and under != layout.ui:
            query = f"?{request.url.query}" if request.url.query else ""
            return RedirectResponse(f"{layout.ui}/{path}{query}", status_code=302)
        if ui is None:
            return HTMLResponse(NO_UI)
        if path:
            file = (ui / path).resolve()
            if file.is_relative_to(ui.resolve()) and file.is_file():
                immutable = path.startswith("assets/")
                return FileResponse(file, headers={"Cache-Control": "public, max-age=31536000, immutable" if immutable else "no-cache"})
        return FileResponse(ui / "index.html", headers={"Cache-Control": "no-cache"})

    return r
