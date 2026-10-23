"""Support sessions: one of this sopops's own support staff working in a customer's workspace to help them.

A support session acts as a member of the customer's org would, **including reading their call transcripts and
changing their SOPs**. It is deliberately narrow:

| | |
|---|---|
| Who can start one | Only an address in SOPOPS_SUPPORT_EMAILS, **proved** by GitHub, Google or an email sign-in link — a password never proves it (`Support.is_staff`). The list is read from the environment on every request; nothing in the app can add to it, so nobody can make themselves support staff. Unset or empty (the default) turns the feature off and the routes answer 404, as if they weren't there. |
| What the customer controls | `orgs.support_access`, on by default. An owner or admin turns it off, which refuses new sessions and ends any that is open against the org right then. |
| How long | `minutes`, 60 by default and 480 at most. The session dies at `mimic_until` whatever happens: it doesn't slide, and using it doesn't extend it. |
| What it can do | Everything a member can, writes included: edit SOPs, publish releases. The role is `member`, so admin routes (members, invites, keys) refuse on their own. |
| What it can't reach | Any org but the one it was started against: others answer 404, as another org's workspaces always do. |
| Who sees it | The customer: every session against their org is listed for them, with who, when, and how many requests it served. |

**The cookies.** The staff member's own `sopops_session` cookie is never touched. A support session lives in a second
pair, `sopops_mimic` and `sopops_mimic_csrf`, and `access.guard` prefers it while it's there and alive. So ending one
(or letting it expire) puts the staff member back in their own account with no re-login.

**The audit trail** (`support_sessions`) is written when a session starts and closed when it ends. Rows are never
deleted, and the staff member's address is copied into the row so a rename can't erase who looked.
"""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlmodel import Session, select

from sqlalchemy import update

from ..errors import Conflict, NotFound
from ..orm import Identity, LoginSession, Org, SupportSession, User, Workspace
from ..tokens import Forbidden
from .service import Current, _hash, _utc

if TYPE_CHECKING:
    from ..store import Store

STAFF_ENV = "SOPOPS_SUPPORT_EMAILS"
DEFAULT_MINUTES = 60
MAX_MINUTES = 480
PROVES_EMAIL = ("github", "google", "email")  # the providers that own the address they hand over
OFF = "support sessions aren't set up on this sopops"
NO_CONSENT = "this team has turned support access off. Ask them to turn it back on before you look"
SUPPORT_OFF = "support access is off"  # why a team can't be picked in the list


def staff() -> list[str]:
    """SOPOPS_SUPPORT_EMAILS: the addresses allowed to start a support session. Read from the environment every time,
    and there's no way to add one through the app."""
    return [e.strip().lower() for e in os.environ.get(STAFF_ENV, "").split(",") if e.strip()]


def enabled() -> bool:
    """Whether this sopops does support sessions at all. Off (the default) the routes answer as if they didn't exist."""
    return bool(staff())


def on_staff_list(email: str | None) -> bool:
    """Whether the address is one of the staff addresses. Being on the list isn't enough to be staff on its own:
    `Support.is_staff` wants the address proved as well. `Accounts.sign_up` and `Accounts.join_with_link` use this to
    turn away someone picking a staff address for a new password account."""
    return bool(email) and enabled() and (email or "").strip().lower() in staff()


def off() -> NotFound:
    return NotFound(OFF)


class Support:
    """Starting, ending, listing and consenting to support sessions."""

    def __init__(self, store: Store):
        self.store = store

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    def is_staff(self, user: User) -> bool:
        """Whether this user may start support sessions: their address is in SOPOPS_SUPPORT_EMAILS, **and** a
        provider that owns the address vouched for it — GitHub, Google, or an email sign-in link sent to it.

        A password is never that proof, however the account came by one, invite included. An invite says an owner
        trusted the address, not that whoever typed the password owns it, and two paths let a stranger pick the
        address outright: a join link and open sign-up (SOPOPS_OPEN_SIGNUP). So on a hosted sopops, `users.email`
        on a password-only account is a claim. Those two paths now refuse a staff address
        (`Accounts.sign_up`, `Accounts.join_with_link`); this check is the other half, for an account that claimed
        one earlier or was made some other way. The proving identity's own address has to match too, so linking a
        provider account for a different address buys nothing."""
        if not on_staff_list(user.email):
            return False
        want = user.email.strip().lower()
        with self._db() as db:
            proofs = db.exec(select(Identity).where(Identity.user_id == user.id, Identity.provider.in_(PROVES_EMAIL))).all()  # type: ignore[attr-defined]
        return any((i.email or "").strip().lower() == want for i in proofs)

    def start(self, cur: Current, target: str, reason: str = "", minutes: int | None = None) -> tuple[dict, str]:
        """Start a support session against the org `target` names (an org id, or a workspace in it). Returns what to
        tell the caller and the session's token, for the `sopops_mimic` cookie.

        Refuses a caller who isn't staff, one already in a support session, and an org that has turned support
        access off. An org nobody knows answers 404, the same as a workspace in another org does. `reason` is
        optional and goes into the audit row as it comes, empty included."""
        if not enabled():
            raise off()
        if not self.is_staff(cur.user):
            raise off()  # the same answer as the feature being off: no hint that there's a staff list to get onto
        if cur.mimicking:
            raise Conflict("you're already in a support session: end it before starting another")
        reason = (reason or "").strip()
        minutes = DEFAULT_MINUTES if minutes is None else int(minutes)
        if minutes < 1 or minutes > MAX_MINUTES:
            raise ValueError(f"minutes must be between 1 and {MAX_MINUTES}")
        now = datetime.now(timezone.utc)
        until = now + timedelta(minutes=minutes)
        token = secrets.token_urlsafe(32)
        with self._db() as db, db.begin():
            org = self._target_org(db, target)
            if not org.support_access:
                raise Forbidden(NO_CONSENT)
            session_id = str(uuid.uuid4())
            db.add(LoginSession(id=session_id, token_hash=_hash(token), user_id=cur.user.id, org_id=org.id, created_at=now,
                                expires_at=until, last_seen_at=now, mimic_org_id=org.id, mimic_reason=reason[:2000], mimic_until=until))
            db.add(SupportSession(id=str(uuid.uuid4()), login_session_id=session_id, staff_user_id=cur.user.id,
                                  staff_email=cur.user.email, org_id=org.id, reason=reason[:2000], started_at=now, until=until))
            out = {"org_id": org.id, "org_name": org.name, "until": until}
        return out, token

    def teams(self) -> list[dict]:
        """Every team on this sopops, for the list a staff member picks one from: its id, name, workspaces and
        whether it allows support sessions at all. Alphabetical by name."""
        with self._db() as db:
            orgs = db.exec(select(Org).order_by(Org.name)).all()  # type: ignore[arg-type]
            spaces: dict[str, list[str]] = {}
            for w in db.exec(select(Workspace).order_by(Workspace.workspace)).all():  # type: ignore[arg-type]
                spaces.setdefault(w.org_id, []).append(w.workspace)
        return [{"id": o.id, "name": o.name, "workspaces": spaces.get(o.id, []), "support_access": bool(o.support_access)} for o in orgs]

    def _target_org(self, db: Session, target: str) -> Org:
        """The org a support session was asked for: `target` is its id, or a workspace in it."""
        target = (target or "").strip()
        org = db.get(Org, target) if target else None
        if org is None and target:
            ws = db.get(Workspace, target)
            org = db.get(Org, ws.org_id) if ws else None
        if org is None:
            raise NotFound(f"no workspace '{target}'")
        return org

    def end(self, token: str | None) -> None:
        """End the support session this token holds: the login session is revoked and the audit row closed. Quiet
        about a token that's unknown or already ended, so the caller can always clear its cookies."""
        if not token:
            return
        now = datetime.now(timezone.utc)
        with self._db() as db, db.begin():
            s = db.exec(select(LoginSession).where(LoginSession.token_hash == _hash(token))).first()
            if s is None or s.mimic_until is None:
                return
            if s.revoked_at is None:
                s.revoked_at = now
                db.add(s)
            row = db.exec(select(SupportSession).where(SupportSession.login_session_id == s.id)).first()
            if row is not None and row.ended_at is None:
                row.ended_at = now
                db.add(row)

    def state(self, cur: Current | None) -> dict:
        """What the banner needs: whether a support session is running, which org it reads and until when, and
        whether whoever is signed in is support staff at all (the UI shows them the start page)."""
        if cur is None or not cur.mimicking:
            return {"running": False, "staff": cur is not None and self.is_staff(cur.user), "org_id": None, "org_name": None, "until": None}
        with self._db() as db:
            org = db.get(Org, cur.mimic_org_id)
        return {"running": True, "staff": True, "org_id": cur.mimic_org_id, "org_name": org.name if org else None, "until": cur.mimic_until}

    def sessions(self, org_id: str) -> list[dict]:
        """Every support session against this org, newest first: what the customer sees."""
        now = datetime.now(timezone.utc)
        with self._db() as db:
            rows = db.exec(select(SupportSession).where(SupportSession.org_id == org_id)
                           .order_by(SupportSession.started_at.desc())).all()  # type: ignore[attr-defined]
        return [{"id": r.id, "staff_email": r.staff_email, "reason": r.reason, "started_at": _utc(r.started_at),
                 "ended_at": _utc(r.ended_at) if r.ended_at else None, "until": _utc(r.until), "requests": r.requests,
                 "last_path": r.last_path, "running": r.ended_at is None and _utc(r.until) > now} for r in rows]

    def access(self, org_id: str) -> bool:
        with self._db() as db:
            org = db.get(Org, org_id)
        if org is None:
            raise NotFound(f"no org '{org_id}'")
        return bool(org.support_access)

    def set_access(self, org_id: str, allowed: bool) -> bool:
        """Turn support access on or off for the org. Off means off now: no new session can start, and any session
        open against the org this minute is revoked and its audit row closed. The switch says stop, not stop within
        the hour. Turning it back on doesn't bring those sessions back."""
        now = datetime.now(timezone.utc)
        with self._db() as db, db.begin():
            org = db.get(Org, org_id)
            if org is None:
                raise NotFound(f"no org '{org_id}'")
            org.support_access = bool(allowed)
            db.add(org)
            if not allowed:
                self._end_live(db, org_id, now)
        return bool(allowed)

    def _end_live(self, db: Session, org_id: str, now: datetime) -> None:
        """Revoke every support session still open against this org, and close its audit row."""
        live = [s for s in db.exec(select(LoginSession).where(LoginSession.mimic_org_id == org_id, LoginSession.revoked_at.is_(None)))  # type: ignore[union-attr]
                if s.mimic_until is not None and _utc(s.mimic_until) > now]
        for s in live:
            s.revoked_at = now
            db.add(s)
        if live:
            db.execute(update(SupportSession).where(SupportSession.login_session_id.in_([s.id for s in live]),  # type: ignore[attr-defined]
                                                    SupportSession.ended_at.is_(None)).values(ended_at=now))  # type: ignore[union-attr]


__all__ = ["DEFAULT_MINUTES", "MAX_MINUTES", "STAFF_ENV", "Support", "enabled", "on_staff_list", "staff"]
