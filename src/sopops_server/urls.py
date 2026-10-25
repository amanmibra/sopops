"""Where people reach this sopops: the web UI's address, the API's, and the path prefix a proxy put in front.

By default both are at the root of one origin: the UI at `/`, the API at `/v1/...`, and nothing here changes anything.
A deployment can move them under paths instead. sopops.dev serves the UI at `/app/` and the API at `/api/`, behind a
router that strips the prefix before forwarding:

- **The UI build says where it lives.** `SOPOPS_WEB_BASE=/app/ SOPOPS_WEB_API_BASE=/api pnpm build` (see
  web/vite.config.ts). The server reads both back from the built index.html (`Layout`), so it knows which prefix is
  the UI's and which the API's without being told twice.
- **A proxy that strips a prefix says which in `X-Forwarded-Prefix`.** `PrefixMiddleware` turns that into the ASGI
  `root_path`, so routing still sees `/v1/...`, while `request.url` and FastAPI's `/docs` see the public path. A request
  that arrives with the prefix still on (no proxy, or one that doesn't strip) is matched against the layout's prefixes
  the same way.
- **The UI serves under its base only.** With a base other than `/`, a request for a UI page outside it (an old
  bookmark of `/w/demo`) is sent to the same page under it (`/app/w/demo`); see `api/ui.py`.
- **Links and redirects.** `SOPOPS_PUBLIC_URL` is the UI's address (`https://sopops.dev/app`): links sopops sends
  (sign-in emails, invites, password links) and redirects back to a page. `SOPOPS_PUBLIC_API_URL` is the API's
  (`https://sopops.dev/api`): sign-in callbacks and the OAuth cookie's path. It defaults to `SOPOPS_PUBLIC_URL`, which
  was always where the callbacks went and still works wherever the API also answers under the UI's address. Unset,
  both come from the request's own scheme and host plus the layout's prefixes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

PUBLIC_URL_ENV = "SOPOPS_PUBLIC_URL"
PUBLIC_API_URL_ENV = "SOPOPS_PUBLIC_API_URL"
PREFIX_HEADER = b"x-forwarded-prefix"

_PREFIX = re.compile(r"^(/[A-Za-z0-9._~-]+)+$")
_ASSET = re.compile(r"""<script[^>]+type="module"[^>]+src="([^"]*?)assets/""")
_API_META = re.compile(r"""<meta\s+name="sopops-api-base"\s+content="([^"]*)\"""")


def clean_prefix(value: str | None) -> str:
    """A path prefix as `/app` (no trailing slash), or "" for the root and for anything that isn't a plain path."""
    value = (value or "").split(",", 1)[0].strip().rstrip("/")
    return value if _PREFIX.fullmatch(value) else ""


def has_prefix(path: str, prefix: str) -> bool:
    return bool(prefix) and (path == prefix or path.startswith(prefix + "/"))


def with_prefix(prefix: str, path: str) -> str:
    """`path` (a path on the UI, `/w/demo`) under `prefix`, unless it already is: `/app/w/demo`. `/` is `/app/`."""
    if not prefix or not path.startswith("/") or path.startswith("//") or has_prefix(path, prefix):
        return path
    return prefix + path


@dataclass(frozen=True)
class Layout:
    """Where the built UI says it lives (`ui`) and where it calls the API (`api`), as prefixes: "" is the root."""

    ui: str = ""
    api: str = ""

    @classmethod
    def of(cls, web: Path | None) -> "Layout":
        """Read from the built index.html: its module script is at `<base>assets/...`, and a build with an API base
        of its own names it in `<meta name="sopops-api-base">`. Without either, the API is under the UI's base,
        which is also what the build assumes."""
        try:
            html = (web / "index.html").read_text(encoding="utf-8") if web else ""
        except OSError:
            html = ""
        asset, meta = _ASSET.search(html), _API_META.search(html)
        ui = clean_prefix(asset.group(1)) if asset else ""
        api = clean_prefix(meta.group(1)) if meta else ui
        return cls(ui=ui, api=api)

    def prefix_of(self, path: str) -> str:
        """Which of the layout's prefixes `path` is under, longest first; "" for none."""
        return next((p for p in sorted({self.ui, self.api}, key=len, reverse=True) if has_prefix(path, p)), "")


class PrefixMiddleware:
    """Sets the ASGI `root_path` from `X-Forwarded-Prefix`, or from the layout when the prefix is still on the path.

    ASGI's `path` includes `root_path`, so a stripped request (`/v1/x` with `X-Forwarded-Prefix: /api`) gets the prefix
    put back on its path; Starlette's routing takes it off again. Only a plain path is taken from the header, and it
    changes nothing but how this one request is routed and the URLs built from it, never the links sopops sends."""

    def __init__(self, app: ASGIApp, layout: Layout) -> None:
        self.app, self.layout = app, layout

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and not scope.get("root_path"):
            header = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == PREFIX_HEADER), None)
            path = scope["path"]
            prefix = clean_prefix(header) or self.layout.prefix_of(path)
            if prefix:
                scope = dict(scope, root_path=prefix)
                if not has_prefix(path, prefix):
                    scope["path"] = prefix + path
                    scope["raw_path"] = prefix.encode() + scope.get("raw_path", path.encode())
        await self.app(scope, receive, send)


def layout(request: Request) -> Layout:
    return getattr(request.app.state, "layout", None) or Layout()


def root_prefix(request: Request) -> str:
    """The prefix this request came in under ("" at the root)."""
    return clean_prefix(request.scope.get("root_path"))


def _origin(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


def public_url(request: Request) -> str:
    """Where people reach the UI, without a trailing slash: SOPOPS_PUBLIC_URL, else this request's scheme and host
    with the UI's prefix."""
    return (os.environ.get(PUBLIC_URL_ENV, "").strip() or _origin(request) + layout(request).ui).rstrip("/")


def public_api_url(request: Request) -> str:
    """Where the browser reaches the API, without a trailing slash: SOPOPS_PUBLIC_API_URL, else SOPOPS_PUBLIC_URL,
    else this request's scheme and host with the API's prefix."""
    explicit = os.environ.get(PUBLIC_API_URL_ENV, "").strip() or os.environ.get(PUBLIC_URL_ENV, "").strip()
    return (explicit or _origin(request) + layout(request).api).rstrip("/")


def ui_path(request: Request, path: str) -> str:
    """A page of the UI (`/sign-in?error=...`) as a path on this site: under the UI's public prefix."""
    return with_prefix(urlsplit(public_url(request)).path.rstrip("/"), path)


def api_path(request: Request, path: str) -> str:
    """An API path (`/v1/auth/callback`) as the browser sees it: under the API's public prefix."""
    return with_prefix(urlsplit(public_api_url(request)).path.rstrip("/"), path)
