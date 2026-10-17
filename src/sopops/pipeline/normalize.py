"""Turn normalization: canonical roles, offsets in seconds, no empty or repeated turns."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

ROLES = {
    "agent": "agent", "assistant": "agent", "bot": "agent", "ai": "agent", "model": "agent",
    "user": "user", "human": "user", "customer": "user", "caller": "user",
    "tool": "tool", "function": "tool", "tool_calls": "tool", "tool_call_result": "tool", "function_call": "tool",
    "function_call_output": "tool", "tool_call_invocation": "tool",
    "system": "system", "developer": "system",
}


def to_utc(at: Any) -> datetime | None:
    """A datetime from ISO 8601, a datetime, or Unix seconds or milliseconds (LiveKit Python sends seconds, Node ms)."""
    if at is None or at == "":
        return None
    if isinstance(at, datetime):
        return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
    if isinstance(at, (int, float)):
        return datetime.fromtimestamp(at / 1000 if at > 1e11 else at, timezone.utc)
    if isinstance(at, str):
        try:
            return to_utc(float(at))
        except ValueError:
            return to_utc(datetime.fromisoformat(at.replace("Z", "+00:00")))
    raise ValueError(f"not a time: {at!r}")


def role(value: Any) -> str:
    return ROLES.get(str(value or "").strip().lower(), "system")


def _offset(item: dict[str, Any], start: datetime | None) -> float | None:
    at = item.pop("at", None)
    if item.get("offset_s") is None and at is not None and start is not None:
        item["offset_s"] = (to_utc(at) - start).total_seconds()
    return round(max(float(item["offset_s"]), 0.0), 3) if item.get("offset_s") is not None else None


def normalize_tool_calls(calls: list[dict[str, Any]], started_at: datetime) -> list[dict[str, Any]]:
    """`offset_s` from `at`, as for turns."""
    start = to_utc(started_at)
    out = []
    for raw in calls:
        c = dict(raw)
        c["offset_s"] = _offset(c, start)
        out.append(c)
    return out


def normalize_turns(turns: list[dict[str, Any]], started_at: datetime) -> list[dict[str, Any]]:
    """Canonical turns, in order. Idempotent: normalizing normalized turns changes nothing.

    - roles mapped to agent | user | tool | system;
    - `offset_s` (seconds since `started_at`) from `at` when missing, rounded to milliseconds; `at` dropped;
    - text stripped; turns with no text dropped, except tool turns;
    - a turn repeating the previous one (same role and text) dropped: LiveKit can emit an item twice;
    - sorted by offset when every turn has one (stable, so equal offsets keep their order).
    """
    start = to_utc(started_at)
    out: list[dict[str, Any]] = []
    for raw in turns:
        t = dict(raw)
        t["role"] = role(t.get("role"))
        t["text"] = str(t.get("text") or "").strip()
        t["offset_s"] = _offset(t, start)
        if not t["text"] and t["role"] != "tool":
            continue
        if out and out[-1]["role"] == t["role"] and out[-1]["text"] == t["text"]:
            continue
        out.append(t)
    if out and all(t["offset_s"] is not None for t in out):
        out.sort(key=lambda t: t["offset_s"])
    return out
