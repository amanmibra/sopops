"""Users, identities, orgs, memberships, invites and login sessions.

**Signing in** (`sign_in`) takes a provider's profile. Its email must be verified, and in SOPOPS_ALLOWED_EMAIL_DOMAINS
when that's set. The identity (provider, provider's user id) finds the user; failing that, a user with the same
verified email gets this identity linked; failing that, a new user is made. Then, for memberships:

- the first person to sign in on a fresh install (no memberships anywhere) becomes owner of the existing org;
- an invite for their email that's still open is accepted;
- an org whose allowed email domains include theirs takes them as a member.

Someone with no membership can sign in but sees only "Ask an owner to invite you".

**Login sessions** are a random token in an HttpOnly cookie, stored as a sha256 hash. They last 30 days from the last
use (sliding; `last_seen_at` and `expires_at` move at most every few minutes) and end early when revoked (sign out).

**Roles.** `owner` and `admin` manage members, invites and the org's settings; `member` does everything else. Only an
owner makes or removes owners, and an org always keeps one owner.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlmodel import Session, select

from ..errors import Conflict, NotFound
from ..orm import Identity, Invite, LoginSession, Membership, Org, User, Workspace
from ..tokens import Forbidden
from . import actor
from .providers import Profile

if TYPE_CHECKING:
    from ..store import Store

OWNER, ADMIN, MEMBER = "owner", "admin", "member"
ROLES = (OWNER, ADMIN, MEMBER)
MANAGERS = (OWNER, ADMIN)
SESSION_DAYS = 30
SLIDE_EVERY = timedelta(minutes=5)
INVITE_DAYS = 7
ALLOWED_DOMAINS_ENV = "SOPOPS_ALLOWED_EMAIL_DOMAINS"


class Refused(Exception):
    """Sign-in refused: no verified email, or an email domain this sopops doesn't allow. The message is safe to show."""


@dataclass(frozen=True)
class Current:
    """A live login session: the user, their memberships, and the org they're working in (None with no membership)."""

    session_id: str
    user: User
    memberships: dict[str, str]  # org id -> role
    org_id: str | None

    @property
    def role(self) -> str | None:
        return self.memberships.get(self.org_id) if self.org_id else None


class Accounts:
    def __init__(self, store: Store):
        self.store = store

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    # --- signing in ----------------------------------------------------------------

    def sign_in(self, profile: Profile) -> tuple[User, str]:
        """The user for this profile (found, linked or made) and a new login session's token. Raises Refused."""
        if not profile.email:
            raise Refused(f"your {profile.provider.title()} account has no verified email address. Verify one there, then sign in again")
        email = profile.email.strip().lower()
        if not server_allows(email):
            raise Refused(f"this sopops doesn't take sign-ins from {domain(email)} addresses")
        now = _now()
        with self._db() as db, db.begin():
            ident = db.exec(select(Identity).where(Identity.provider == profile.provider, Identity.provider_user_id == profile.provider_user_id)).first()
            user = db.get(User, ident.user_id) if ident else None
            if user is None:
                user = db.exec(select(User).where(User.email == email)).first()  # the same verified email links
            if user is None:
                user = User(id=str(uuid.uuid4()), email=email, name=profile.name or email.split("@")[0], avatar_url=profile.avatar_url, created_at=now)
                db.add(user)
                db.flush()
            if ident is None:
                db.add(Identity(id=str(uuid.uuid4()), user_id=user.id, provider=profile.provider, provider_user_id=profile.provider_user_id,
                                email=email, created_at=now))
            else:
                ident.email = email
                db.add(ident)
            user.name = profile.name or user.name
            user.avatar_url = profile.avatar_url or user.avatar_url
            user.last_login_at = now
            db.add(user)
            self._join(db, user, email, now)
            token = self._new_session(db, user.id, now)
        return user, token

    def _join(self, db: Session, user: User, email: str, now: datetime) -> None:
        """First user on a fresh install: owner. Then open invites for the email, and orgs allowing its domain."""
        if db.exec(select(func.count()).select_from(Membership)).one() == 0:
            db.add(Membership(org_id=actor.default_org(db), user_id=user.id, role=OWNER, created_at=now))
            db.flush()
        mine = set(db.exec(select(Membership.org_id).where(Membership.user_id == user.id)))
        for inv in db.exec(select(Invite).where(Invite.email == email, Invite.accepted_at.is_(None), Invite.revoked_at.is_(None))):  # type: ignore[union-attr]
            if _utc(inv.expires_at) <= now:
                continue
            if inv.org_id not in mine:
                db.add(Membership(org_id=inv.org_id, user_id=user.id, role=inv.role, created_at=now))
                mine.add(inv.org_id)
            inv.accepted_at, inv.accepted_by = now, user.id
            db.add(inv)
        d = domain(email)
        for org in db.exec(select(Org)):
            if org.id not in mine and d in (org.allowed_domains or []):
                db.add(Membership(org_id=org.id, user_id=user.id, role=MEMBER, created_at=now))
                mine.add(org.id)

    def _new_session(self, db: Session, user_id: str, now: datetime) -> str:
        token = secrets.token_urlsafe(32)
        db.add(LoginSession(id=str(uuid.uuid4()), token_hash=_hash(token), user_id=user_id, created_at=now,
                            expires_at=now + timedelta(days=SESSION_DAYS), last_seen_at=now))
        return token

    # --- login sessions ------------------------------------------------------------

    def current(self, token: str | None) -> Current | None:
        """The live session for this cookie token, slid forward; None if there's none, or it expired or was revoked."""
        if not token:
            return None
        now = _now()
        with self._db() as db:
            s = db.exec(select(LoginSession).where(LoginSession.token_hash == _hash(token))).first()
            if s is None or s.revoked_at is not None or _utc(s.expires_at) <= now:
                return None
            user = db.get(User, s.user_id)
            if user is None:
                return None
            roles = {m.org_id: m.role for m in db.exec(select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at))}  # type: ignore[arg-type]
            if now - _utc(s.last_seen_at) >= SLIDE_EVERY:
                s.last_seen_at, s.expires_at = now, now + timedelta(days=SESSION_DAYS)
                db.add(s)
                db.commit()
        org = s.org_id if s.org_id in roles else next(iter(roles), None)
        return Current(session_id=s.id, user=user, memberships=roles, org_id=org)

    def sign_out(self, token: str | None) -> None:
        if not token:
            return
        with self._db() as db, db.begin():
            s = db.exec(select(LoginSession).where(LoginSession.token_hash == _hash(token))).first()
            if s is not None and s.revoked_at is None:
                s.revoked_at = _now()
                db.add(s)

    def switch_org(self, cur: Current, org_id: str) -> None:
        if org_id not in cur.memberships:
            raise NotFound(f"you're not in org '{org_id}'")
        with self._db() as db, db.begin():
            s = db.get(LoginSession, cur.session_id)
            if s is not None:
                s.org_id = org_id
                db.add(s)

    # --- orgs -----------------------------------------------------------------------

    def orgs(self, ids: list[str]) -> list[Org]:
        if not ids:
            return []
        with self._db() as db:
            got = {o.id: o for o in db.exec(select(Org).where(Org.id.in_(ids)))}  # type: ignore[attr-defined]
        return [got[i] for i in ids if i in got]

    def org(self, org_id: str) -> Org:
        with self._db() as db:
            org = db.get(Org, org_id)
        if org is None:
            raise NotFound(f"no org '{org_id}'")
        return org

    def workspace_org(self, workspace: str) -> str | None:
        """The org a workspace belongs to; None if there's no such workspace yet."""
        with self._db() as db:
            w = db.get(Workspace, workspace)
        return w.org_id if w else None

    def update_org(self, org_id: str, name: str | None = None, allowed_domains: list[str] | None = None) -> Org:
        with self._db() as db, db.begin():
            org = db.get(Org, org_id)
            if org is None:
                raise NotFound(f"no org '{org_id}'")
            if name is not None:
                if not name.strip():
                    raise ValueError("the org needs a name")
                org.name = name.strip()[:128]
            if allowed_domains is not None:
                org.allowed_domains = clean_domains(allowed_domains)
            db.add(org)
        return org

    # --- members --------------------------------------------------------------------

    def members(self, org_id: str) -> list[dict]:
        with self._db() as db:
            rows = db.exec(select(Membership, User).where(Membership.org_id == org_id, Membership.user_id == User.id).order_by(Membership.created_at))  # type: ignore[arg-type]
            return [{"user_id": u.id, "email": u.email, "name": u.name, "avatar_url": u.avatar_url, "role": m.role, "joined_at": _utc(m.created_at),
                     "last_login_at": _utc(u.last_login_at) if u.last_login_at else None} for m, u in rows]

    def set_role(self, org_id: str, by_role: str, user_id: str, role: str) -> dict:
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        with self._db() as db, db.begin():
            m = db.get(Membership, (org_id, user_id))
            if m is None:
                raise NotFound(f"no member '{user_id}'")
            if OWNER in (role, m.role) and by_role != OWNER:
                raise Forbidden("only an owner can make or change an owner")
            if m.role == OWNER and role != OWNER:
                self._keep_an_owner(db, org_id, user_id)
            m.role = role
            db.add(m)
        return next(x for x in self.members(org_id) if x["user_id"] == user_id)

    def remove_member(self, org_id: str, by_role: str, user_id: str) -> None:
        with self._db() as db, db.begin():
            m = db.get(Membership, (org_id, user_id))
            if m is None:
                raise NotFound(f"no member '{user_id}'")
            if m.role == OWNER:
                if by_role != OWNER:
                    raise Forbidden("only an owner can remove an owner")
                self._keep_an_owner(db, org_id, user_id)
            db.delete(m)

    def _keep_an_owner(self, db: Session, org_id: str, leaving: str) -> None:
        others = db.exec(select(func.count()).select_from(Membership).where(Membership.org_id == org_id, Membership.role == OWNER, Membership.user_id != leaving)).one()
        if others == 0:
            raise Conflict("an org needs an owner: make someone else owner first")

    # --- invites --------------------------------------------------------------------

    def invites(self, org_id: str) -> list[dict]:
        """The org's invites that are still open, newest first."""
        now = _now()
        with self._db() as db:
            rows = db.exec(select(Invite).where(Invite.org_id == org_id, Invite.accepted_at.is_(None), Invite.revoked_at.is_(None))  # type: ignore[union-attr]
                           .order_by(Invite.created_at.desc()))  # type: ignore[attr-defined]
            return [invite_out(i) for i in rows if _utc(i.expires_at) > now]

    def invite(self, org_id: str, by: str, by_role: str, email: str, role: str = MEMBER) -> tuple[dict, str]:
        """A new invite and its token (returned here only)."""
        email = (email or "").strip().lower()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise ValueError("that isn't an email address")
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        if role == OWNER and by_role != OWNER:
            raise Forbidden("only an owner can invite an owner")
        token = secrets.token_urlsafe(32)
        now = _now()
        with self._db() as db, db.begin():
            member = db.exec(select(Membership).join(User, Membership.user_id == User.id).where(Membership.org_id == org_id, User.email == email)).first()  # type: ignore[arg-type]
            if member is not None:
                raise Conflict(f"{email} is already a member")
            inv = Invite(id=str(uuid.uuid4()), org_id=org_id, email=email, role=role, token_hash=_hash(token), invited_by=by, created_at=now,
                         expires_at=now + timedelta(days=INVITE_DAYS))
            db.add(inv)
        return invite_out(inv), token

    def revoke_invite(self, org_id: str, id: str) -> None:
        with self._db() as db, db.begin():
            inv = db.get(Invite, id)
            if inv is None or inv.org_id != org_id:
                raise NotFound(f"no invite '{id}'")
            if inv.revoked_at is None and inv.accepted_at is None:
                inv.revoked_at = _now()
                db.add(inv)

    def invite_info(self, token: str, user_id: str | None = None) -> dict:
        """What an invite link is for, to show before accepting. NotFound for an unknown, withdrawn or expired one, or
        one used by someone other than `user_id` (`accepted` says it was used by them)."""
        with self._db() as db:
            inv = db.exec(select(Invite).where(Invite.token_hash == _hash(token))).first()
            mine = inv is not None and inv.accepted_at is not None and user_id is not None and inv.accepted_by == user_id
            if inv is None or inv.revoked_at is not None or (inv.accepted_at is not None and not mine) or (not mine and _utc(inv.expires_at) <= _now()):
                raise NotFound("this invite link has expired, was used, or was withdrawn. Ask for a new one")
            org = db.get(Org, inv.org_id)
        return {"org_id": inv.org_id, "org_name": org.name if org else "", "email": inv.email, "role": inv.role, "expires_at": _utc(inv.expires_at),
                "accepted": mine}

    def accept(self, token: str, cur: Current) -> str:
        """Join the invite's org as the signed-in user, whose email must be the invite's. Returns the org id."""
        info = self.invite_info(token, cur.user.id)
        if info["accepted"]:
            return info["org_id"]
        if cur.user.email != info["email"]:
            raise Forbidden(f"this invite is for {info['email']}, and you're signed in as {cur.user.email}")
        now = _now()
        with self._db() as db, db.begin():
            inv = db.exec(select(Invite).where(Invite.token_hash == _hash(token))).one()
            if db.get(Membership, (inv.org_id, cur.user.id)) is None:
                db.add(Membership(org_id=inv.org_id, user_id=cur.user.id, role=inv.role, created_at=now))
            inv.accepted_at, inv.accepted_by = now, cur.user.id
            db.add(inv)
            s = db.get(LoginSession, cur.session_id)
            if s is not None:
                s.org_id = inv.org_id
                db.add(s)
        return info["org_id"]


def invite_out(i: Invite) -> dict:
    return {"id": i.id, "email": i.email, "role": i.role, "created_at": _utc(i.created_at), "expires_at": _utc(i.expires_at)}


def csrf_token(session_token: str) -> str:
    """The CSRF token for a session: derived from the session's own token, so only its holder can know it."""
    return hmac.new(session_token.encode(), b"sopops-csrf", hashlib.sha256).hexdigest()


def domain(email: str) -> str:
    return email.rpartition("@")[2].lower()


def clean_domains(domains: list[str]) -> list[str]:
    out: list[str] = []
    for d in domains:
        d = d.strip().lower().lstrip("@")
        if not d:
            continue
        if "." not in d or any(c in d for c in " /@:"):
            raise ValueError(f"'{d}' isn't an email domain (like example.com)")
        if d not in out:
            out.append(d)
    return out


def server_allows(email: str) -> bool:
    """SOPOPS_ALLOWED_EMAIL_DOMAINS (comma-separated), when set, is the only domains anyone signs in from."""
    allowed = [d.strip().lower().lstrip("@") for d in os.environ.get(ALLOWED_DOMAINS_ENV, "").split(",") if d.strip()]
    return not allowed or domain(email) in allowed


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime) -> datetime:
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
