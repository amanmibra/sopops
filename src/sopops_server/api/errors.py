"""Exceptions from the layers below, as HTTP responses."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import forms
from ..auth import access
from ..errors import Ambiguous, Conflict, NotFound
from ..github import GitHubError
from ..insights import BadFilter
from ..sessions import BadCursor
from ..sopc import Invalid, SopcUnavailable
from ..tokens import Forbidden, Unauthorized


def register(app: FastAPI) -> None:
    @app.exception_handler(Invalid)
    def _invalid(_, exc: Invalid) -> JSONResponse:
        return JSONResponse(status_code=422, content={"valid": False, "issues": [forms.locate(i).to_dict() for i in exc.issues]})

    @app.exception_handler(NotFound)
    def _not_found(_, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(Conflict)
    def _conflict(_, exc: Conflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(Ambiguous)
    def _ambiguous(_, exc: Ambiguous) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(SopcUnavailable)
    def _sopc_unavailable(_, exc: SopcUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(Unauthorized)
    def _unauthorized(request: Request, exc: Unauthorized) -> JSONResponse:
        out = JSONResponse(status_code=401, content={"detail": str(exc)}, headers={"WWW-Authenticate": "Bearer"})
        if getattr(request.state, "stale_login", False) and access.SIGNED_IN_COOKIE in request.cookies:
            access.clear_signed_in_cookie(out, request)  # the session is gone, so the landing page shouldn't say "Open app"
        return out

    @app.exception_handler(Forbidden)
    def _forbidden(_, exc: Forbidden) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(BadCursor)
    def _bad_cursor(_, exc: BadCursor) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(BadFilter)
    def _bad_filter(_, exc: BadFilter) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(GitHubError)
    def _github(_, exc: GitHubError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})
