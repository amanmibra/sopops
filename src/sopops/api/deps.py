"""What every router shares."""

from __future__ import annotations

from ..schemas import ValidateResponse

OPEN: list = []  # no authentication yet (v0) on the management endpoints; keep sopops on a private network
INVALID = {422: {"model": ValidateResponse, "description": "The files don't compile; sopc's issues."}}
