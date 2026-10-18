"""Users, identities, orgs, memberships, invites, email sign-in links, passwords and login sessions.

**Signing in** (`sign_in`) takes a provider's profile. Its email must be verified, and in SOPOPS_ALLOWED_EMAIL_DOMAINS
when that's set. The identity (provider, provider's user id) finds the user; failing that, a user with the same
verified email gets this identity linked; failing that, a new user is made. Then, for memberships:

- the first person to sign in on a fresh install (no memberships anywhere) becomes owner of the existing org;
- an invite for their email that's still open is accepted;
- an org whose allowed email domains include theirs takes them as a member.

Someone with no membership can sign in but sees only "Ask an owner to invite you".

**Email sign-in links** (`request_link`, `use_link`) are a random token, stored as a sha256 hash, that works once, for
15 minutes. Using one signs in as the `email` provider, whose identity is the address: the same rules as above, so it
links to a GitHub or Google user with that verified email. Each address gets at most 5 links per 15 minutes, and each
client IP 20; past that, and for addresses this sopops doesn't take, no link is made, and the caller says the same thing
either way. Using a link also uses up the address's other open links.

**Passwords** (`password_login`, `first_account`, `join_with_password`, `password_link`, `use_password_link`,
`change_password`; hashing and the rules are in `sopops.auth.password`). The first account on a fresh install (no users
at all) is made with a password and owns the org. After that, a password is set only through an invite for an address
with no account yet, or a password link: one an owner or admin makes for a member, one `sopops reset-password` prints,
or one emailed to the address ("Forgot password?", and with SOPOPS_ALLOWED_EMAIL_DOMAINS set, making an account at
those domains). A password link is a random token, stored as a sha256 hash, that works once, for an hour. Setting a
password through a link, or changing it, ends the user's other login sessions. Signing in with a password allows 10
failed tries per address and 30 per client IP in 15 minutes, and says the same thing for an unknown address and a wrong
password.

**Login sessions** are a random token in an HttpOnly cookie, stored as a sha256 hash. They last 30 days from the last
use (sliding; `last_seen_at` and `expires_at` move at most every few minutes) and end early when revoked (sign out).

**Roles.** `owner` and `admin` manage members, invites and the org's settings; `member` does everything else. Only an
owner makes, changes or removes an owner, and an org always keeps one owner.

**Adding people** (`add_people`) by email: someone who has an account joins at once; anyone else gets an invite, and
joins the moment they sign in with that email, any way. A new invite for an address replaces its open one.
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

from sqlalchemy import delete, func, update
from sqlmodel import Session, select

from ..errors import Conflict, NotFound
from ..orm import EmailLink, Identity, Invite, LoginAttempt, LoginSession, Membership, Org, PasswordLink, User, Workspace
from ..tokens import Forbidden
from . import actor, password
from .providers import Profile

if TYPE_CHECKING:
    from ..store import Store

OWNER, ADMIN, MEMBER = "owner", "admin", "member"
ROLES = (OWNER, ADMIN, MEMBER)
MANAGERS = (OWNER, ADMIN)
SESSION_DAYS = 30
SLIDE_EVERY = timedelta(minutes=5)
INVITE_DAYS = 7
LINK_TTL = timedelta(minutes=15)
LINK_WINDOW = timedelta(minutes=15)
LINKS_PER_EMAIL = 5
LINKS_PER_IP = 20
ALLOWED_DOMAINS_ENV = "SOPOPS_ALLOWED_EMAIL_DOMAINS"
PASSWORD = "password"
PASSWORD_LINK_TTL = timedelta(hours=1)
TRIES_WINDOW = timedelta(minutes=15)
TRIES_PER_EMAIL = 10
TRIES_PER_IP = 30
WRONG_PASSWORD = "That email and password don't match."


class LinkInvalid(Exception):
    """An email sign-in link that's unknown, used or expired."""


class Refused(Exception):
    """Sign-in refused: no verified email, or an email domain this sopops doesn't allow. The message is safe to show."""


class WrongPassword(Exception):
    """A password sign-in that didn't match: the same for an unknown address."""


class TooManyTries(Exception):
    """Too many failed password sign-ins for the address or the client, in the last 15 minutes."""


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

    # --- email sign-in links ------------------------------------------------------

    def request_link(self, email: str, ip: str | None, next: str = "/") -> str | None:
        """A new sign-in link's token for this address, or None when it gets none: an address this sopops doesn't take,
        or over the rate limit. Raises ValueError for something that isn't an email address."""
        email = clean_email(email)
        if not server_allows(email):
            return None
        now = _now()
        since = now - LINK_WINDOW
        with self._db() as db, db.begin():
            by_email = db.exec(select(func.count()).select_from(EmailLink).where(EmailLink.email == email, EmailLink.created_at > since)).one()
            by_ip = db.exec(select(func.count()).select_from(EmailLink).where(EmailLink.ip == ip, EmailLink.created_at > since)).one() if ip else 0
            if by_email >= LINKS_PER_EMAIL or by_ip >= LINKS_PER_IP:
                return None
            token = secrets.token_urlsafe(32)
            db.add(EmailLink(id=str(uuid.uuid4()), token_hash=_hash(token), email=email, ip=ip[:64] if ip else None, next=next[:512], created_at=now,
                             expires_at=now + LINK_TTL))
        return token

    def use_link(self, token: str) -> tuple[User, str, str]:
        """Sign in with an email link: the user, a new login session's token, and the page to open. The link is used up
        here, even if signing in is then refused. Raises LinkInvalid or Refused."""
        now = _now()
        with self._db() as db, db.begin():
            link = db.exec(select(EmailLink).where(EmailLink.token_hash == _hash(token or ""))).first()
            if link is None or link.used_at is not None or _utc(link.expires_at) <= now:
                raise LinkInvalid("this sign-in link has expired or was used already")
            used = db.execute(update(EmailLink).where(EmailLink.id == link.id, EmailLink.used_at.is_(None)).values(used_at=now))  # type: ignore[union-attr, arg-type]
            if used.rowcount != 1:  # opened twice at once: only one wins
                raise LinkInvalid("this sign-in link has expired or was used already")
            db.execute(update(EmailLink).where(EmailLink.email == link.email, EmailLink.used_at.is_(None)).values(used_at=now))  # type: ignore[union-attr, arg-type]
            email, next = link.email, link.next
        user, session = self.sign_in(Profile(provider="email", provider_user_id=email, email=email))
        return user, session, next

    # --- passwords ------------------------------------------------------------------

    def password_login(self, email: str, secret: str, ip: str | None) -> tuple[User, str]:
        """Sign in with a password: the user and a new login session's token. Raises WrongPassword or TooManyTries.
        The try is written down before the password is checked, so many at once still count."""
        email = (email or "").strip().lower()[:320]
        now = _now()
        since = now - TRIES_WINDOW
        with self._db() as db, db.begin():
            by_email = db.exec(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.email == email, LoginAttempt.created_at > since)).one()
            by_ip = db.exec(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.ip == ip, LoginAttempt.created_at > since)).one() if ip else 0
            if by_email >= TRIES_PER_EMAIL or by_ip >= TRIES_PER_IP:
                raise TooManyTries("too many tries. Wait 15 minutes, then try again")
            db.add(LoginAttempt(id=str(uuid.uuid4()), email=email, ip=ip[:64] if ip else None, created_at=now))
        with self._db() as db:
            user = db.exec(select(User).where(User.email == email)).first()
            ident = self._password_identity(db, user.id) if user else None
        ok = password.verify(ident.password_hash if ident else None, secret)  # an unknown address checks a dummy hash
        if not ok or user is None or ident is None or not server_allows(email):
            raise WrongPassword(WRONG_PASSWORD)
        with self._db() as db, db.begin():
            db.execute(delete(LoginAttempt).where(LoginAttempt.email == email))  # type: ignore[arg-type]
            if password.needs_rehash(ident.password_hash or ""):
                ident.password_hash = password.hash(secret)
                db.add(ident)
        return self.sign_in(Profile(provider=PASSWORD, provider_user_id=user.id, email=email))

    def has_users(self) -> bool:
        with self._db() as db:
            return db.exec(select(User.id).limit(1)).first() is not None

    def has_password(self, user_id: str) -> bool:
        with self._db() as db:
            return self._password_identity(db, user_id) is not None

    def first_account(self, email: str, name: str, secret: str) -> tuple[User, str]:
        """On a fresh install (no users at all), make the first account with a password. It owns the org. Raises
        Conflict once anyone has an account, ValueError for a bad address or password, Refused for a domain this
        sopops doesn't take."""
        email = clean_email(email)
        password.check(secret, email)
        if not server_allows(email):
            raise Refused(f"this sopops doesn't take sign-ins from {domain(email)} addresses")
        with self._db() as db, db.begin():
            if db.exec(select(User.id).limit(1)).first() is not None:
                raise Conflict("someone already made the first account. Sign in, or ask an owner to invite you")
            user = self._new_password_user(db, email, name, secret)
        return self.sign_in(Profile(provider=PASSWORD, provider_user_id=user.id, email=email))

    def join_with_password(self, token: str, name: str, secret: str) -> tuple[User, str]:
        """Accept an invite by making an account with a password. Only for an address with no account: anyone else
        signs in the way they always do, which accepts it. Raises NotFound, Conflict, ValueError or Refused."""
        info = self.invite_info(token)
        email = info["email"]
        password.check(secret, email)
        if not server_allows(email):
            raise Refused(f"this sopops doesn't take sign-ins from {domain(email)} addresses")
        with self._db() as db, db.begin():
            if db.exec(select(User).where(User.email == email)).first() is not None:
                raise Conflict(f"{email} already has an account. Sign in to join")
            user = self._new_password_user(db, email, name, secret)
        return self.sign_in(Profile(provider=PASSWORD, provider_user_id=user.id, email=email))  # accepts the invite

    def _new_password_user(self, db: Session, email: str, name: str, secret: str) -> User:
        now = _now()
        user = User(id=str(uuid.uuid4()), email=email, name=(name or "").strip()[:256] or email.split("@")[0], created_at=now)
        db.add(user)
        db.flush()
        db.add(Identity(id=str(uuid.uuid4()), user_id=user.id, provider=PASSWORD, provider_user_id=user.id, email=email,
                        password_hash=password.hash(secret), created_at=now))
        return user

    def _password_identity(self, db: Session, user_id: str) -> Identity | None:
        return db.exec(select(Identity).where(Identity.user_id == user_id, Identity.provider == PASSWORD)).first()

    def change_password(self, cur: Current, current: str, new: str) -> None:
        """Change the signed-in user's password. The current one must match; the user's other login sessions end.
        Raises WrongPassword, TooManyTries or ValueError."""
        email = cur.user.email
        password.check(new, email)
        with self._db() as db:
            ident = self._password_identity(db, cur.user.id)
        if ident is None:
            raise ValueError("you don't have a password here: you sign in another way")
        self._count_try(email, None)
        if not password.verify(ident.password_hash, current):
            raise WrongPassword("that isn't your current password")
        with self._db() as db, db.begin():
            db.execute(delete(LoginAttempt).where(LoginAttempt.email == email))  # type: ignore[arg-type]
            ident = db.get(Identity, ident.id)
            if ident is not None:
                ident.password_hash = password.hash(new)
                db.add(ident)
            self._end_sessions(db, cur.user.id, keep=cur.session_id)

    def _count_try(self, email: str, ip: str | None) -> None:
        now = _now()
        with self._db() as db, db.begin():
            tries = db.exec(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.email == email, LoginAttempt.created_at > now - TRIES_WINDOW)).one()
            if tries >= TRIES_PER_EMAIL:
                raise TooManyTries("too many tries. Wait 15 minutes, then try again")
            db.add(LoginAttempt(id=str(uuid.uuid4()), email=email, ip=ip[:64] if ip else None, created_at=now))

    def password_link(self, org_id: str, by: str, by_role: str, user_id: str) -> tuple[dict, str]:
        """A link that sets a member's password, made by an owner or admin: the member's email and when it expires,
        and the token. Only an owner acts on an owner, and someone who's also in another org needs a manager there
        too (else an admin here could take over their account in an org they don't run)."""
        with self._db() as db, db.begin():
            m = db.get(Membership, (org_id, user_id))
            user = db.get(User, user_id)
            if m is None or user is None:
                raise NotFound(f"no member '{user_id}'")
            if m.role == OWNER and by_role != OWNER:
                raise Forbidden("only an owner can reset an owner's password")
            elsewhere = set(db.exec(select(Membership.org_id).where(Membership.user_id == user_id, Membership.org_id != org_id)))
            if elsewhere:
                managed = set(db.exec(select(Membership.org_id).where(Membership.user_id == by, Membership.role.in_(MANAGERS))))  # type: ignore[attr-defined]
                if not elsewhere <= managed:
                    raise Forbidden(f"{user.email} is also in another org, so they reset it themselves, or whoever runs sopops does (`sopops reset-password`)")
            row, token = self._new_password_link(db, user.email, by=by)
        return {"email": user.email, "expires_at": _utc(row.expires_at)}, token

    def cli_password_link(self, email: str) -> tuple[dict, str]:
        """A link that sets the password for an account, for `sopops reset-password`. Raises NotFound."""
        email = (email or "").strip().lower()
        with self._db() as db, db.begin():
            if db.exec(select(User).where(User.email == email)).first() is None:
                raise NotFound(f"no account for {email}")
            row, token = self._new_password_link(db, email)
        return {"email": email, "expires_at": _utc(row.expires_at)}, token

    def request_password_link(self, email: str, ip: str | None) -> tuple[str, bool] | None:
        """For "Forgot password?" and making an account at SOPOPS_ALLOWED_EMAIL_DOMAINS: a link's token for this
        address and whether it has an account, or None when it gets none (no account and sign-up isn't open to its
        domain, or over the rate limit: 5 per address and 20 per client per 15 minutes). The caller says the same
        thing either way. Raises ValueError for something that isn't an email address."""
        email = clean_email(email)
        if not server_allows(email):
            return None
        now = _now()
        since = now - LINK_WINDOW
        with self._db() as db, db.begin():
            exists = db.exec(select(User.id).where(User.email == email)).first() is not None
            if not exists and not sign_up_open(email):
                return None
            by_email = db.exec(select(func.count()).select_from(PasswordLink).where(PasswordLink.email == email, PasswordLink.created_at > since)).one()
            by_ip = db.exec(select(func.count()).select_from(PasswordLink).where(PasswordLink.ip == ip, PasswordLink.created_at > since)).one() if ip else 0
            if by_email >= LINKS_PER_EMAIL or by_ip >= LINKS_PER_IP:
                return None
            _, token = self._new_password_link(db, email, ip=ip)
        return token, exists

    def _new_password_link(self, db: Session, email: str, by: str | None = None, ip: str | None = None) -> tuple[PasswordLink, str]:
        now = _now()
        token = secrets.token_urlsafe(32)
        row = PasswordLink(id=str(uuid.uuid4()), token_hash=_hash(token), email=email, ip=ip[:64] if ip else None, created_by=by, created_at=now,
                           expires_at=now + PASSWORD_LINK_TTL)
        db.add(row)
        return row, token

    def password_link_info(self, token: str) -> dict:
        """What a password link is for, to show before using it. Raises LinkInvalid."""
        with self._db() as db:
            link = self._open_password_link(db, token)
            user = db.exec(select(User).where(User.email == link.email)).first()
        return {"email": link.email, "has_account": user is not None, "expires_at": _utc(link.expires_at)}

    def _open_password_link(self, db: Session, token: str) -> PasswordLink:
        link = db.exec(select(PasswordLink).where(PasswordLink.token_hash == _hash(token or ""))).first()
        if link is None or link.used_at is not None or _utc(link.expires_at) <= _now():
            raise LinkInvalid("this link has expired or was used already")
        return link

    def use_password_link(self, token: str, secret: str, name: str = "") -> tuple[User, str]:
        """Set the password with a link: the account is made if there's none yet (only an emailed link can do that),
        every login session the user had ends, and a new one starts. Raises LinkInvalid, ValueError or Refused."""
        now = _now()
        with self._db() as db, db.begin():
            link = self._open_password_link(db, token)
            password.check(secret, link.email)
            if not server_allows(link.email):
                raise Refused(f"this sopops doesn't take sign-ins from {domain(link.email)} addresses")
            used = db.execute(update(PasswordLink).where(PasswordLink.id == link.id, PasswordLink.used_at.is_(None)).values(used_at=now))  # type: ignore[union-attr, arg-type]
            if used.rowcount != 1:  # used twice at once: only one wins
                raise LinkInvalid("this link has expired or was used already")
            db.execute(update(PasswordLink).where(PasswordLink.email == link.email, PasswordLink.used_at.is_(None)).values(used_at=now))  # type: ignore[union-attr, arg-type]
            email = link.email
            user = db.exec(select(User).where(User.email == email)).first()
            if user is None:
                user = self._new_password_user(db, email, name, secret)  # an emailed link: they opened it, so the address is theirs
            else:
                ident = self._password_identity(db, user.id)
                if ident is None:
                    db.add(Identity(id=str(uuid.uuid4()), user_id=user.id, provider=PASSWORD, provider_user_id=user.id, email=email,
                                    password_hash=password.hash(secret), created_at=now))
                else:
                    ident.password_hash = password.hash(secret)
                    db.add(ident)
                self._end_sessions(db, user.id)
            db.execute(delete(LoginAttempt).where(LoginAttempt.email == email))  # type: ignore[arg-type]
        return self.sign_in(Profile(provider=PASSWORD, provider_user_id=user.id, email=email))

    def _end_sessions(self, db: Session, user_id: str, keep: str | None = None) -> None:
        q = update(LoginSession).where(LoginSession.user_id == user_id, LoginSession.revoked_at.is_(None))  # type: ignore[union-attr, arg-type]
        if keep is not None:
            q = q.where(LoginSession.id != keep)  # type: ignore[arg-type]
        db.execute(q.values(revoked_at=_now()))

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
        """A new invite and its token (returned here only). It replaces the address's open invite, if there's one."""
        email = clean_email(email)
        _check_role(by_role, role)
        with self._db() as db, db.begin():
            if self._member_by_email(db, org_id, email) is not None:
                raise Conflict(f"{email} is already a member")
            inv, token = self._new_invite(db, org_id, by, email, role)
        return invite_out(inv), token

    def _member_by_email(self, db: Session, org_id: str, email: str) -> Membership | None:
        return db.exec(select(Membership).join(User, Membership.user_id == User.id).where(Membership.org_id == org_id, User.email == email)).first()  # type: ignore[arg-type]

    def _new_invite(self, db: Session, org_id: str, by: str, email: str, role: str) -> tuple[Invite, str]:
        now = _now()
        for old in db.exec(select(Invite).where(Invite.org_id == org_id, Invite.email == email, Invite.accepted_at.is_(None), Invite.revoked_at.is_(None))):  # type: ignore[union-attr]
            old.revoked_at = now
            db.add(old)
        token = secrets.token_urlsafe(32)
        inv = Invite(id=str(uuid.uuid4()), org_id=org_id, email=email, role=role, token_hash=_hash(token), invited_by=by, created_at=now,
                     expires_at=now + timedelta(days=INVITE_DAYS))
        db.add(inv)
        return inv, token

    def add_people(self, org_id: str, by: str, by_role: str, emails: list[str], role: str = MEMBER) -> list[dict]:
        """Add people by email: `added` (they have an account, and joined now), `invited` (with the invite's `token`;
        they join when they sign in with that email), or `already` (a member)."""
        _check_role(by_role, role)
        wanted: list[str] = []
        for e in emails:
            e = clean_email(e)
            if e not in wanted:
                wanted.append(e)
        if not wanted:
            raise ValueError("add at least one email address")
        now = _now()
        out: list[dict] = []
        with self._db() as db, db.begin():
            for email in wanted:
                if self._member_by_email(db, org_id, email) is not None:
                    out.append({"email": email, "status": "already"})
                    continue
                user = db.exec(select(User).where(User.email == email)).first()
                if user is not None:
                    db.add(Membership(org_id=org_id, user_id=user.id, role=role, created_at=now))
                    out.append({"email": email, "status": "added", "user_id": user.id, "name": user.name})
                    continue
                inv, token = self._new_invite(db, org_id, by, email, role)
                out.append({"email": email, "status": "invited", "invite": invite_out(inv), "token": token})
        return out

    def resend_invite(self, org_id: str, id: str) -> tuple[dict, str]:
        """A fresh token and 7 more days for an open invite (the old link stops working)."""
        now = _now()
        token = secrets.token_urlsafe(32)
        with self._db() as db, db.begin():
            inv = db.get(Invite, id)
            if inv is None or inv.org_id != org_id or inv.revoked_at is not None or inv.accepted_at is not None:
                raise NotFound(f"no open invite '{id}'")
            inv.token_hash, inv.created_at, inv.expires_at = _hash(token), now, now + timedelta(days=INVITE_DAYS)
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
            has_account = db.exec(select(User.id).where(User.email == inv.email)).first() is not None
        return {"org_id": inv.org_id, "org_name": org.name if org else "", "email": inv.email, "role": inv.role, "expires_at": _utc(inv.expires_at),
                "accepted": mine, "has_account": has_account}

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


def _check_role(by_role: str, role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    if role == OWNER and by_role != OWNER:
        raise Forbidden("only an owner can make someone an owner")


def clean_email(email: str) -> str:
    """The address, trimmed and lowercased. Raises ValueError for something that isn't one."""
    email = (email or "").strip().lower()
    local, _, d = email.rpartition("@")
    if not local or "." not in d or len(email) > 320 or any(c.isspace() or c in "<>,;\"" for c in email):
        raise ValueError(f"'{email[:64]}' isn't an email address")
    return email


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
    allowed = sign_up_domains()
    return not allowed or domain(email) in allowed


def sign_up_domains() -> list[str]:
    return [d.strip().lower().lstrip("@") for d in os.environ.get(ALLOWED_DOMAINS_ENV, "").split(",") if d.strip()]


def sign_up_open(email: str) -> bool:
    """Whether someone with no account can make one with this address by email link: only at the domains in
    SOPOPS_ALLOWED_EMAIL_DOMAINS (with it unset, people join through invites)."""
    return domain(email) in sign_up_domains()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime) -> datetime:
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
