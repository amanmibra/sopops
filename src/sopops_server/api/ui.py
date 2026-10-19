"""The web UI: built assets, and index.html for every other path so the client-side router can take it."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

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


def router() -> APIRouter:
    """Mounted last: it answers every path the API doesn't."""
    r = APIRouter()
    ui = web_dir()

    @r.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request) -> Response:
        if path == "v1" or path.startswith("v1/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        if ui is None:
            return HTMLResponse(NO_UI)
        if path:
            file = (ui / path).resolve()
            if file.is_relative_to(ui.resolve()) and file.is_file():
                immutable = path.startswith("assets/")
                return FileResponse(file, headers={"Cache-Control": "public, max-age=31536000, immutable" if immutable else "no-cache"})
        return FileResponse(ui / "index.html", headers={"Cache-Control": "no-cache"})

    return r
