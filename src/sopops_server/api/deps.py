"""What every router shares."""

from __future__ import annotations

from ..schemas import ValidateResponse

OPEN: list = []  # nothing per route: the app-wide guard (sopops_server.auth.access) authenticates every route by its kind
INVALID = {422: {"model": ValidateResponse, "description": "The files don't compile; sopc's issues."}}
