"""Errors every layer raises, and how ids are checked. No imports from the rest of sopops, so any module can use it."""

from __future__ import annotations

import re

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class NotFound(Exception):
    """404."""


class Conflict(Exception):
    """409."""


def workspace_id(workspace: str) -> str:
    """The workspace id, if it's a safe one (letters, digits, `.`, `_`, `-`; up to 128); else NotFound."""
    if not SAFE_ID.match(workspace):
        raise NotFound(f"invalid workspace id '{workspace}'")
    return workspace
