"""Exceptions from the layers below, as HTTP responses."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .. import forms
from ..errors import Conflict, NotFound
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

    @app.exception_handler(SopcUnavailable)
    def _sopc_unavailable(_, exc: SopcUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(Unauthorized)
    def _unauthorized(_, exc: Unauthorized) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(exc)}, headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(Forbidden)
    def _forbidden(_, exc: Forbidden) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(BadCursor)
    def _bad_cursor(_, exc: BadCursor) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(BadFilter)
    def _bad_filter(_, exc: BadFilter) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
