"""Who a request acts as, for the layers below the API: the signed-in user (or nobody), and the org new workspaces go
into.

`access.guard` sets `current` on every request. Where an author is recorded (file versions, releases, verdicts,
suggestions, finding overrides, rule labels, imports and tokens), a row written while someone is signed in gets their user id in
`author_id` and their name in the text column, whatever name the request sent (and so does a suggestion when it's
applied, sent as a pull request or dismissed). With no sign-in, rows keep the name they were given and no `author_id`.
This module imports nothing from sopops but the tables, so any layer can use it.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlmodel import Session, select

from ..orm import FileVersion, FindingOverride, ImportRecord, Org, Release, RuleLabel, Suggestion, Token, Verdict


@dataclass(frozen=True)
class Actor:
    user_id: str
    name: str
    email: str
    org_id: str | None  # the org the request works in: the workspace's, else the session's
    role: str | None  # in that org


current: ContextVar[Actor | None] = ContextVar("sopops_actor", default=None)

# Each authored table, and its column with the name.
AUTHOR_COLUMN = {FileVersion: "author", Release: "author", Verdict: "reviewer", Suggestion: "reviewer", FindingOverride: "reviewer", RuleLabel: "reviewer",
                 ImportRecord: "created_by", Token: "created_by"}
DEFAULT_ORG = "Default"
DECIDED = ("applied", "pr_open", "dismissed")


def get() -> Actor | None:
    return current.get()


def workspace_org(db: Session) -> str:
    """The org a new workspace goes into: the signed-in user's, else the first org (made, named Default, if there's
    none)."""
    a = current.get()
    if a is not None and a.org_id:
        return a.org_id
    return default_org(db)


def default_org(db: Session) -> str:
    org = db.exec(select(Org).order_by(Org.created_at, Org.id)).first()  # type: ignore[arg-type]
    if org is not None:
        return org.id
    org = Org(id=str(uuid.uuid4()), name=DEFAULT_ORG, slug=slug(DEFAULT_ORG), allowed_domains=[], created_at=datetime.now(timezone.utc))
    db.add(org)
    db.flush()
    return org.id


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64] or "org"


def _stamp(_mapper, _conn, row) -> None:
    a = current.get()
    if a is None:
        return
    if isinstance(row, Verdict) and row.carried_from:  # a recluster carrying a verdict over keeps who gave it
        return
    row.author_id = a.user_id
    setattr(row, AUTHOR_COLUMN[type(row)], a.name or a.email)


def _stamp_decision(mapper, conn, row: Suggestion) -> None:
    """A suggestion applied, sent as a pull request or dismissed records who decided."""
    if sa_inspect(row).attrs.status.history.has_changes() and row.status in DECIDED:
        _stamp(mapper, conn, row)


for _table in AUTHOR_COLUMN:
    event.listen(_table, "before_insert", _stamp)
event.listen(Suggestion, "before_update", _stamp_decision)
