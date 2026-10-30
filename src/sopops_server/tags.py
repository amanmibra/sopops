"""Tags: labels on agents (`pilot`, `beta`, `team:support`) that sessions copy when they come in, to filter by.

An agent's tags are set in sopops, not in its sop file, so changing them needs no release. A session keeps the tags
it came in with (its agent's at that moment, plus any its sender passed in `tags` or `sopops_tags` metadata): changing
an agent's tags later leaves its past sessions as they were. A session's own tags can be edited too.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from sqlmodel import Session, delete, select

from .errors import InvalidValue
from .orm import AgentTag, SessionTag

MAX_TAGS = 20
META_KEY = "sopops_tags"
_TAG = re.compile(r"^[a-z0-9][a-z0-9_.:/-]{0,63}$")


def normalize(tag: str) -> str:
    """Lowercase, trimmed, spaces as dashes: `Night Shift` is `night-shift`."""
    return re.sub(r"\s+", "-", tag.strip().lower())


def clean(tags: Iterable[Any], strict: bool = True) -> list[str]:
    """Tags normalized, in order, without repeats. A bad one raises InvalidValue, or with `strict` off is dropped."""
    out: list[str] = []
    for raw in tags:
        tag = normalize(raw) if isinstance(raw, str) else ""
        if not _TAG.match(tag):
            if strict:
                raise InvalidValue(f"'{raw}' isn't a tag: use letters, digits and - _ . : /, up to 64 characters")
            continue
        if tag not in out:
            out.append(tag)
    if len(out) > MAX_TAGS:
        if strict:
            raise InvalidValue(f"at most {MAX_TAGS} tags")
        out = out[:MAX_TAGS]
    return out


def from_metadata(meta: Mapping[str, Any] | None) -> list[str]:
    """`sopops_tags` (or `{"sopops": {"tags": ...}}`) in a session's metadata: a list, or one comma-separated string."""
    meta = meta or {}
    nested = meta.get("sopops")
    raw = meta.get(META_KEY, nested.get("tags") if isinstance(nested, Mapping) else None)
    if isinstance(raw, str):
        raw = raw.split(",")
    return clean(raw, strict=False) if isinstance(raw, (list, tuple)) else []


def for_agent(db: Session, ws: str, agent: str | None) -> list[str]:
    if not agent:
        return []
    return sorted(db.exec(select(AgentTag.tag).where(AgentTag.workspace == ws, AgentTag.agent == agent)))


def by_agent(db: Session, ws: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in db.exec(select(AgentTag).where(AgentTag.workspace == ws).order_by(AgentTag.agent, AgentTag.tag)):
        out.setdefault(row.agent, []).append(row.tag)
    return out


def set_agent(db: Session, ws: str, agent: str, tags: list[str]) -> None:
    db.exec(delete(AgentTag).where(AgentTag.workspace == ws, AgentTag.agent == agent))  # type: ignore[call-overload]
    db.add_all(AgentTag(workspace=ws, agent=agent, tag=t) for t in tags)


def for_sessions(db: Session, ids: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {i: [] for i in ids}
    if ids:
        for sid, tag in db.exec(select(SessionTag.session_id, SessionTag.tag).where(SessionTag.session_id.in_(ids)).order_by(SessionTag.tag)):
            out[sid].append(tag)
    return out


def add_to_session(db: Session, ws: str, session_id: str, tags: Iterable[str]) -> None:
    """Add tags a session doesn't have yet; never removes one."""
    have = set(db.exec(select(SessionTag.tag).where(SessionTag.session_id == session_id)))
    db.add_all(SessionTag(session_id=session_id, tag=t, workspace_id=ws) for t in dict.fromkeys(tags) if t not in have)


def set_session(db: Session, ws: str, session_id: str, tags: list[str]) -> None:
    db.exec(delete(SessionTag).where(SessionTag.session_id == session_id))  # type: ignore[call-overload]
    db.add_all(SessionTag(session_id=session_id, tag=t, workspace_id=ws) for t in tags)


def used(db: Session, ws: str) -> list[str]:
    """Every tag an agent or a session in the workspace has, for filter pickers."""
    agent = set(db.exec(select(AgentTag.tag).where(AgentTag.workspace == ws).distinct()))
    session = set(db.exec(select(SessionTag.tag).where(SessionTag.workspace_id == ws).distinct()))
    return sorted(agent | session)


def has_all(ws: str, tags: Iterable[str], S: Any) -> list[Any]:
    """Where-clauses: the session (`S`, sessions or an alias) has every one of `tags`."""
    return [S.id.in_(select(SessionTag.session_id).where(SessionTag.workspace_id == ws, SessionTag.tag == t)) for t in tags]
