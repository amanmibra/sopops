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


PHONE = re.compile(r"^\+?[\d\s().-]{7,}$")


def platform_id_key(platform_id: str) -> str:
    """How a platform id is compared. Teams that identify agents by phone number paste whichever form they
    have on hand — `+1 (415) 555-0123`, `+14155550123`, `1-415-555-0123` — so a number is compared by its
    digits alone. Anything else (a sopc id, a UUID, a short numeric id) is compared as written, ignoring case."""
    value = platform_id.strip()
    if PHONE.match(value):
        return "+" + re.sub(r"\D", "", value)
    return value.casefold()


def same_ref(a: str, b: str) -> bool:
    """Whether two platform refs (`livekit:+14155550123`) name the same agent, however the number is written."""
    pa, _, ia = a.strip().rpartition(":")
    pb, _, ib = b.strip().rpartition(":")
    return pa.casefold() == pb.casefold() and platform_id_key(ia) == platform_id_key(ib)
