"""Users, identities, orgs, memberships, invites, email sign-in links, passwords and login sessions.

**Signing in** (`sign_in`) takes a provider's profile. Its email must be verified, and in SOPOPS_ALLOWED_EMAIL_DOMAINS
when that's set. The identity (provider, provider's user id) finds the user; failing that, a user with the same
verified email gets this identity linked; failing that, a new user is made. Then, for memberships:

- the first person to sign in on a fresh install (no memberships anywhere) becomes owner of the existing org;
- an invite for their email that's still open is accepted;
- an org whose allowed email domains include theirs takes them as a member.

Someone with no membership can sign in but sees only "Ask an owner to invite you".

**A provider that owns the teams** (`owns_teams`: PropelAuth) brings them with it instead, and sign-in mirrors them
(`_mirror_orgs`). Each team it reports is found or made here, matched on its id there (`orgs.external_id`), and the
person joins it with the role it gave. One it stops reporting they leave, so leaving a team there takes effect here;
one made in sopops is never renamed, deleted or left on its say-so, because it never knew about it. A team made from
sopops's own screens through such a provider's API comes back the same way (`adopt_team`): it is made there, and
mirrored here by the same rules, so the next sign-in agrees with what the person already sees. The fresh-install
rule above doesn't apply either: those teams are the teams. Nothing changes for GitHub, Google, email or password,
which report none, and a sopops that uses them keeps its invites and join links exactly as they are.

**Email sign-in links** (`request_link`, `use_link`) are a random token, stored as a sha256 hash, that works once, for
15 minutes. Using one signs in as the `email` provider, whose identity is the address: the same rules as above, so it
links to a GitHub or Google user with that verified email. Each address gets at most 5 links per 15 minutes, and each
client IP 20; past that, and for addresses this sopops doesn't take, no link is made, and the caller says the same thing
either way. Using a link also uses up the address's other open links.

**Passwords** (`password_login`, `first_account`, `join_with_password`, `password_link`, `use_password_link`,
`change_password`; hashing and the rules are in `sopops_server.auth.password`). The first account on a fresh install (no users
at all) is made with a password and owns the org. After that, a password is set only through an invite for an address
with no account yet, or a password link: one an owner or admin makes for a member, one `sopops reset-password` prints,
or one emailed to the address ("Forgot password?", and with SOPOPS_ALLOWED_EMAIL_DOMAINS set, making an account at
those domains). A password link is a random token, stored as a sha256 hash, that works once, for an hour. Setting a
password through a link, or changing it, ends the user's other login sessions. Signing in with a password allows 10
failed tries per address and 30 per client IP in 15 minutes, and says the same thing for an unknown address and a wrong
password.

**Login sessions** are a random token in an HttpOnly cookie, stored as a sha256 hash. They last 30 days from the last
use (sliding; `last_seen_at` and `expires_at` move at most every few minutes) and end early when revoked (sign out).
A session signed in through a provider that runs its own session (PropelAuth) also keeps that provider's refresh
token, encrypted with SOPOPS_SECRET_KEY, so `sign_out` can end their session before revoking this one. Only an
explicit sign-out does that: a session that simply expires, or that `_end_sessions` revokes (a password change, say),
leaves the provider's session alone, and the person may be signed back in without a prompt.

**Roles.** `owner` and `admin` manage members, invites and the org's settings; `member` does everything else. Only an
owner makes, changes or removes an owner, and an org always keeps one owner.

**Adding people** (`add_people`) by email: someone who has an account joins at once; anyone else gets an invite, and
joins the moment they sign in with that email, any way. A new invite for an address replaces its open one.

**Join links** (`new_join_link`, `join_with_link`, `join_by_link`): each org has at most one link that works, not tied
to an email, that anyone can open to join with the link's role (member or admin): by making an account with a name,
email and password, or, already signed in, by joining (which adds the org; it never takes another away). It works
until it expires (7 days unless asked otherwise, at most 90) or an owner or admin turns it off or makes a new one. The
token is a random string stored as a sha256 hash, shown once. Making an account with one counts as a try against the
password rate limits (10 per address and 30 per client in 15 minutes).

**Teams.** People see an org as a team with one workspace (`team_workspace`), named after the team when it's made
(`northwind`, or `northwind-2` when that's taken: workspace ids are unique across orgs). The first run names the org after
the team and makes its workspace (`set_up`); anyone in an org can make another team (`create_team`), which they own.
With SOPOPS_OPEN_SIGNUP on, anyone can make an account with a password and a team of their own at once (`sign_up`).
Nobody checks that they own the address, so sign-up refuses an address that an open invite or a team's email domains
are waiting for, and joins no team but the new one.
Older installs can have several workspaces in an org: the team works in the one changed most recently, and the others
stay reachable by their address. **Platforms** (`platforms`, `set_platforms`): the voice platforms the
team uses, so the app shows only those. An org that never picked (NULL) shows them all, and so does one that picked
only "something else".
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, update
from sqlmodel import Session, select

from ..errors import Conflict, NotFound
from ..orm import EmailLink, FileVersion, Identity, Invite, JoinLink, LoginAttempt, LoginSession, Membership, Org, PasswordLink, User, Workspace
from ..runtime import crypto
from ..tokens import Forbidden
from . import actor, password, propelauth
from .providers import ADMIN, MEMBER, OWNER, ROLES, Profile, ProviderOrg  # the roles live with the provider contract: one maps onto them
from . import providers

if TYPE_CHECKING:
    from ..store import Store

log = logging.getLogger("sopops.auth")

MANAGERS = (OWNER, ADMIN)
SESSION_DAYS = 30
SLIDE_EVERY = timedelta(minutes=5)
INVITE_DAYS = 7
JOIN_DAYS = 7
JOIN_MAX_DAYS = 90
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
PLATFORMS = ("livekit", "elevenlabs", "vapi")
OTHER = "other"


class LinkInvalid(Exception):
    """An email sign-in link that's unknown, used or expired."""


def _label(provider: str) -> str:
    """What to call a provider in a message: its own label, so GitHub and PropelAuth keep their capitals."""
    known = providers.get(provider)
    return known.label if known else provider.title()


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
            raise Refused(f"your {_label(profile.provider)} account has no verified email address. Verify one there, then sign in again")
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
            p = providers.get(profile.provider)
            owns_teams = p is not None and p.owns_teams  # PropelAuth: its teams are the teams, mirrored on every sign-in
            self._join(db, user, email, now, claim_default=not owns_teams)
            if owns_teams:
                self._mirror_orgs(db, user, profile, now)
            token = self._new_session(db, user.id, now, profile.refresh_token)
        return user, token

    def _mirror_orgs(self, db: Session, user: User, profile: Profile, now: datetime) -> None:
        """Make sopops's teams match the ones the provider owns: find or make each team it reports, put this person
        in it with the role it gave them, and take away a membership of one it no longer reports, so leaving a team
        there leaves it here.

        Teams are matched on the provider's own org id, kept as `Org.external_id`, so renaming one there renames
        this one rather than making a second. Two things are out of bounds. A team made in sopops (no `external_id`,
        or one from another provider) is never renamed, never deleted and never taken away from anybody: the
        provider was never asked about it, so its silence says nothing. And a mirrored team outlives its last
        member: the workspace and its files stay, and the provider can put somebody back in it tomorrow.
        """
        mine = {m.org_id: m for m in db.exec(select(Membership).where(Membership.user_id == user.id))}
        theirs = {self._mirror_team(db, user, profile.provider, team, now, mine).id for team in profile.orgs}
        for org_id, m in mine.items():
            if org_id in theirs:
                continue
            org = db.get(Org, org_id)
            if org is not None and (org.external_id or "").startswith(f"{profile.provider}:"):
                db.delete(m)
        db.flush()

    def _mirror_team(self, db: Session, user: User, provider: str, team: ProviderOrg, now: datetime,
                     mine: dict[str, Membership]) -> Org:
        """One team of the provider's, found or made here, with this person in it at the role it gave. `mine` is
        their memberships, by org id, so a sign-in mirroring a dozen teams reads them once.

        This is the whole of what mirroring one team means, and both callers want all of it: the sign-in that
        mirrors everything the provider reports (`_mirror_orgs`), and the team somebody just made there from a
        sopops screen (`adopt_team`), which must land exactly where the next sign-in would put it."""
        key = _external_id(provider, team.id)
        org = db.exec(select(Org).where(Org.external_id == key)).first()
        if org is None:
            org, _ = _new_team(db, user.id, team.name, now, role=team.role, external_id=key)
            return org
        if team.name and team.name != org.name:
            org.name = team.name  # theirs to name; the slug and the workspace keep the names they were made with
            db.add(org)
        m = mine.get(org.id)
        if m is None:
            db.add(Membership(org_id=org.id, user_id=user.id, role=team.role, created_at=now))
        elif m.role != team.role:
            m.role = team.role
            db.add(m)
        return org

    def _join(self, db: Session, user: User, email: str, now: datetime, claim_default: bool = True) -> None:
        """First user on a fresh install: owner. Then open invites for the email, and orgs allowing its domain.

        `claim_default` is off for a provider that owns the teams (`_mirror_orgs`): its teams are the teams, so the
        first person to sign in doesn't walk away owning a default org that nobody there has heard of."""
        if claim_default and db.exec(select(func.count()).select_from(Membership)).one() == 0:
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

    def _new_session(self, db: Session, user_id: str, now: datetime, refresh_token: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        db.add(LoginSession(id=str(uuid.uuid4()), token_hash=_hash(token), user_id=user_id, created_at=now,
                            expires_at=now + timedelta(days=SESSION_DAYS), last_seen_at=now,
                            refresh_ciphertext=_seal(refresh_token)))
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
            since = now - TRIES_WINDOW
            tries = db.exec(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.email == email, LoginAttempt.created_at > since)).one()
            by_ip = db.exec(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.ip == ip, LoginAttempt.created_at > since)).one() if ip else 0
            if tries >= TRIES_PER_EMAIL or by_ip >= TRIES_PER_IP:
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
        """End the session. A session that kept a provider refresh token (PropelAuth) ends the provider's session
        first, so the next click on "Sign in" asks who they are instead of letting them straight back in.

        That call can't stop a sign-out: PropelAuth being unreachable, slow or unhappy is logged and the session is
        revoked anyway (`propelauth.end_session` swallows it), as is a refresh token this SOPOPS_SECRET_KEY can no
        longer read."""
        if not token:
            return
        with self._db() as db:
            s = db.exec(select(LoginSession).where(LoginSession.token_hash == _hash(token))).first()
            sealed = s.refresh_ciphertext if s is not None and s.revoked_at is None else None
        if sealed:
            propelauth.end_session(_unseal(sealed))
        with self._db() as db, db.begin():
            s = db.exec(select(LoginSession).where(LoginSession.token_hash == _hash(token))).first()
            if s is not None and s.revoked_at is None:
                s.revoked_at = _now()
                s.refresh_ciphertext = None  # spent: it's no good to anyone now, so it isn't kept
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

    def set_up(self, org_id: str, team: str) -> tuple[Org, str]:
        """Name the org after the team (its slug too, when that's free), and make the team's workspace, named after
        it, unless the org has one already. Returns the org and its workspace."""
        team = _team_name(team)
        with self._db() as db, db.begin():
            org = db.get(Org, org_id)
            if org is None:
                raise NotFound(f"no org '{org_id}'")
            org.name = team
            org.slug = _free(actor.slug(team), lambda s: s == org.slug or db.exec(select(Org.id).where(Org.slug == s)).first() is None)
            db.add(org)
            ws = self._team_workspace(db, org_id) or _new_workspace(db, org_id, team)
        return org, ws

    def create_team(self, cur: Current, team: str) -> tuple[Org, str]:
        """A new org named `team`, owned by the signed-in user, with its one workspace; their login session works in
        it now. Their other orgs stay as they are."""
        team = _team_name(team)
        with self._db() as db, db.begin():
            org, ws = _new_team(db, cur.user.id, team, _now())
            s = db.get(LoginSession, cur.session_id)
            if s is not None:
                s.org_id = org.id
                db.add(s)
        return org, ws

    def adopt_team(self, cur: Current, provider: str, team: ProviderOrg) -> tuple[Org, str]:
        """Mirror a team the provider has just made for this person and work in it now, as `create_team` does for a
        team made here. Their other teams stay as they are: this says nothing about any team but the new one.

        It goes through the same mirroring as a sign-in (`_mirror_team`), so the team is keyed on the provider's own
        id for it and the next sign-in finds it rather than making a second one."""
        with self._db() as db, db.begin():
            user = db.get(User, cur.user.id)
            if user is None:
                raise NotFound(f"no user '{cur.user.id}'")
            mine = {m.org_id: m for m in db.exec(select(Membership).where(Membership.user_id == user.id))}
            org = self._mirror_team(db, user, provider, team, _now(), mine)
            db.flush()
            ws = self._team_workspace(db, org.id) or _new_workspace(db, org.id, org.name)
            s = db.get(LoginSession, cur.session_id)
            if s is not None:
                s.org_id = org.id
                db.add(s)
        return org, ws

    def provider_user_id(self, user_id: str, provider: str) -> str | None:
        """What this provider calls this person, if they've ever signed in with it. Its API speaks in its own ids,
        so making a team there, or changing somebody's role, needs this one and not sopops's."""
        with self._db() as db:
            ident = db.exec(select(Identity).where(Identity.user_id == user_id, Identity.provider == provider)).first()
        return ident.provider_user_id if ident else None

    def team_workspaces(self, org_ids: list[str]) -> dict[str, str]:
        """Each org's workspace, the one its team works in: the one changed most recently, else the first by name.
        Orgs with none are left out. (A team has one workspace; older installs can have several, and the others stay
        reachable by their address.)"""
        if not org_ids:
            return {}
        with self._db() as db:
            return {o: w for o in org_ids if (w := self._team_workspace(db, o))}

    def _team_workspace(self, db: Session, org_id: str) -> str | None:
        last = select(FileVersion.workspace, func.max(FileVersion.created_at).label("at")).group_by(FileVersion.workspace).subquery()  # type: ignore[arg-type]
        rows = db.exec(select(Workspace.workspace, last.c.at).outerjoin(last, last.c.workspace == Workspace.workspace).where(Workspace.org_id == org_id)).all()  # type: ignore[arg-type, call-overload]
        if not rows:
            return None
        changed = [(_utc(at), w) for w, at in rows if at is not None]
        if changed:
            top = max(at for at, _ in changed)
            return min(w for at, w in changed if at == top)
        return min(w for w, _ in rows)

    def platforms(self, org_id: str | None) -> dict:
        """The platforms the org picked (None: never asked) and the ones the app shows. With no org given (sign-in
        off), the first org's."""
        with self._db() as db:
            org = db.get(Org, org_id) if org_id else db.exec(select(Org).order_by(Org.created_at, Org.id)).first()  # type: ignore[arg-type]
        return platforms_out(org.platforms if org else None)

    def set_platforms(self, org_id: str | None, platforms: list[str]) -> dict:
        """Pick the platforms the org uses. With no org given (sign-in off), the first org's (made if there's none)."""
        picked = clean_platforms(platforms)
        with self._db() as db, db.begin():
            org = db.get(Org, org_id or actor.default_org(db))
            if org is None:
                raise NotFound(f"no org '{org_id}'")
            org.platforms = picked
            db.add(org)
        return platforms_out(picked)

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
        check_role(by_role, role)
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
        check_role(by_role, role)
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

    # --- join links -------------------------------------------------------------------

    def join_link(self, org_id: str) -> dict | None:
        """The org's join link that works, if there's one (without its token, which isn't kept)."""
        with self._db() as db:
            link = self._live_join_link(db, org_id)
            return join_link_out(link) if link else None

    def _live_join_link(self, db: Session, org_id: str) -> JoinLink | None:
        now = _now()
        rows = db.exec(select(JoinLink).where(JoinLink.org_id == org_id, JoinLink.revoked_at.is_(None)).order_by(JoinLink.created_at.desc()))  # type: ignore[union-attr, attr-defined]
        return next((j for j in rows if _utc(j.expires_at) > now), None)

    def new_join_link(self, org_id: str, by: str, role: str = MEMBER, days: int = JOIN_DAYS) -> tuple[dict, str]:
        """A new join link and its token (returned here only). The org's old one stops working."""
        if role not in (ADMIN, MEMBER):
            raise ValueError("a join link makes people members or admins")
        if not 1 <= days <= JOIN_MAX_DAYS:
            raise ValueError(f"a join link works for 1 to {JOIN_MAX_DAYS} days")
        now = _now()
        token = secrets.token_urlsafe(32)
        with self._db() as db, db.begin():
            self._end_join_links(db, org_id, now)
            link = JoinLink(id=str(uuid.uuid4()), org_id=org_id, token_hash=_hash(token), role=role, created_by=by, created_at=now,
                            expires_at=now + timedelta(days=days))
            db.add(link)
        return join_link_out(link), token

    def end_join_link(self, org_id: str) -> None:
        """Turn the org's join link off."""
        with self._db() as db, db.begin():
            self._end_join_links(db, org_id, _now())

    def _end_join_links(self, db: Session, org_id: str, now: datetime) -> None:
        db.execute(update(JoinLink).where(JoinLink.org_id == org_id, JoinLink.revoked_at.is_(None)).values(revoked_at=now))  # type: ignore[union-attr, arg-type]

    def _open_join_link(self, db: Session, token: str) -> JoinLink:
        link = db.exec(select(JoinLink).where(JoinLink.token_hash == _hash(token or ""))).first()
        if link is None or link.revoked_at is not None or _utc(link.expires_at) <= _now():
            raise LinkInvalid("this join link has expired or was turned off")
        return link

    def join_link_info(self, token: str, user_id: str | None = None) -> dict:
        """What a join link is for, to show before joining: the org, the role, and whether `user_id` is in it already.
        Raises LinkInvalid."""
        with self._db() as db:
            link = self._open_join_link(db, token)
            org = db.get(Org, link.org_id)
            member = user_id is not None and db.get(Membership, (link.org_id, user_id)) is not None
        return {"org_id": link.org_id, "org_name": org.name if org else "", "role": link.role, "expires_at": _utc(link.expires_at), "member": member,
                "needs_platforms": org is not None and org.platforms is None}

    def join_with_link(self, token: str, email: str, name: str, secret: str, ip: str | None) -> tuple[User, str]:
        """Make an account with a password and join the link's org, which the new login session works in. Only for an
        address with no account: anyone else signs in, then joins. Raises LinkInvalid, Conflict, ValueError, Refused or
        TooManyTries."""
        email = clean_email(email)
        with self._db() as db:
            self._open_join_link(db, token)
        password.check(secret, email)
        if not server_allows(email):
            raise Refused(f"this sopops doesn't take sign-ins from {domain(email)} addresses")
        self._count_try(email, ip)
        now = _now()
        with self._db() as db, db.begin():
            link = self._open_join_link(db, token)
            if db.exec(select(User).where(User.email == email)).first() is not None:
                raise Conflict(f"{email} already has an account. Sign in, then open the link again to join")
            user = self._new_password_user(db, email, name, secret)
            db.add(Membership(org_id=link.org_id, user_id=user.id, role=link.role, created_at=now))
            link.joined += 1
            db.add(link)
            org_id = link.org_id
        user, session = self.sign_in(Profile(provider=PASSWORD, provider_user_id=user.id, email=email))
        self._work_in(session, org_id)
        return user, session

    def sign_up(self, email: str, name: str, secret: str, team: str, ip: str | None) -> tuple[User, str, Org, str]:
        """Open sign-up (SOPOPS_OPEN_SIGNUP): make an account with a password, and a new team it owns with its one
        workspace, which the new login session works in. Returns the user, the session's token, the org and its
        workspace. Raises ValueError, Refused, TooManyTries, or Conflict (nobody has an account yet, so this is the
        first run; the address has an account; or an invite or a team's email domains are waiting for it, which only
        someone who shows they own the address should get)."""
        email = clean_email(email)
        team = _team_name(team)
        password.check(secret, email)
        if not server_allows(email):
            raise Refused(f"this sopops doesn't take sign-ins from {domain(email)} addresses")
        self._count_try(email, ip)
        now = _now()
        with self._db() as db, db.begin():
            if db.exec(select(User.id).limit(1)).first() is None:
                raise Conflict("nobody has an account here yet. Make the first one at /welcome")
            if db.exec(select(User.id).where(User.email == email)).first() is not None:
                raise Conflict(f"there's an account for {email} already. Sign in instead")
            if self._waiting_for(db, email, now):
                raise Conflict("this address can't sign up here. Ask a team owner or admin for the team's join link")
            user = self._new_password_user(db, email, name, secret)
            org, ws = _new_team(db, user.id, team, now)
            user.last_login_at = now
            db.add(user)
            token = self._new_session(db, user.id, now)
        self._work_in(token, org.id)
        return user, token, org, ws

    def _waiting_for(self, db: Session, email: str, now: datetime) -> bool:
        """An open invite for the address, or a team that takes everyone at its domain."""
        invites = db.exec(select(Invite).where(Invite.email == email, Invite.accepted_at.is_(None), Invite.revoked_at.is_(None))).all()  # type: ignore[union-attr]
        if any(_utc(i.expires_at) > now for i in invites):
            return True
        d = domain(email)
        return any(d in (o.allowed_domains or []) for o in db.exec(select(Org)))

    def join_by_link(self, token: str, cur: Current) -> str:
        """Join the link's org as the signed-in user, and work in it. Their other orgs, and their role in this one if
        they're in it already, stay as they are. Returns the org id. Raises LinkInvalid."""
        now = _now()
        with self._db() as db, db.begin():
            link = self._open_join_link(db, token)
            if db.get(Membership, (link.org_id, cur.user.id)) is None:
                db.add(Membership(org_id=link.org_id, user_id=cur.user.id, role=link.role, created_at=now))
                link.joined += 1
                db.add(link)
            s = db.get(LoginSession, cur.session_id)
            if s is not None:
                s.org_id = link.org_id
                db.add(s)
            return link.org_id

    def _work_in(self, session_token: str, org_id: str) -> None:
        with self._db() as db, db.begin():
            s = db.exec(select(LoginSession).where(LoginSession.token_hash == _hash(session_token))).first()
            if s is not None:
                s.org_id = org_id
                db.add(s)

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


def check_role(by_role: str, role: str) -> None:
    """Whether somebody with `by_role` may hand out `role`. Public because the API layer checks it too, for the
    invites that go to a provider that owns the teams instead of into sopops's own table."""
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


def join_link_out(j: JoinLink) -> dict:
    return {"id": j.id, "role": j.role, "joined": j.joined, "created_at": _utc(j.created_at), "expires_at": _utc(j.expires_at)}


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


def clean_platforms(platforms: list[str]) -> list[str]:
    """The picks, known ones only, in the usual order. Raises ValueError for an unknown one or none at all."""
    picked = {p.strip().lower() for p in platforms}
    unknown = picked - {*PLATFORMS, OTHER}
    if unknown:
        raise ValueError(f"unknown platform '{sorted(unknown)[0]}': pick from {', '.join((*PLATFORMS, OTHER))}")
    if not picked:
        raise ValueError("pick at least one platform, or 'other'")
    return [p for p in (*PLATFORMS, OTHER) if p in picked]


def platforms_out(picked: list[str] | None) -> dict:
    """What an org picked, and what the app shows: the known platforms it picked, or all of them when it picked none."""
    shown = [p for p in PLATFORMS if p in (picked or [])]
    return {"selected": picked, "shown": shown or list(PLATFORMS)}


def _team_name(team: str) -> str:
    team = (team or "").strip()[:128]
    if not team:
        raise ValueError("the team needs a name")
    return team


def _new_team(db: Session, user_id: str, team: str, now: datetime, role: str = OWNER, external_id: str | None = None) -> tuple[Org, str]:
    """A new org named `team` (with a slug nobody has), with the user in it, and its one workspace. `role` is theirs
    in it, `owner` for a team somebody makes here. `external_id` marks a team mirrored from a provider that owns the
    teams (`Accounts._mirror_orgs`); a team made in sopops leaves it None."""
    slug = _free(actor.slug(team), lambda s: db.exec(select(Org.id).where(Org.slug == s)).first() is None)
    org = Org(id=str(uuid.uuid4()), name=team, slug=slug, allowed_domains=[], external_id=external_id, created_at=now)
    db.add(org)
    db.flush()
    db.add(Membership(org_id=org.id, user_id=user_id, role=role, created_at=now))
    return org, _new_workspace(db, org.id, team)


def _external_id(provider: str, org_id: str) -> str:
    """How a mirrored team names the provider's own: the provider's id too, so two of them can't collide."""
    return f"{provider}:{org_id}"


def _new_workspace(db: Session, org_id: str, team: str) -> str:
    """A team's workspace, named after the team (`northwind`, or `northwind-2` when that's taken, in any org)."""
    base = actor.slug(team) if actor.slug(team) != "org" else "team"
    ws = _free(base, lambda w: db.get(Workspace, w) is None)
    db.add(Workspace(workspace=ws, org_id=org_id))
    return ws


def _free(base: str, free) -> str:
    """`base`, or `base-2`, `base-3`… : the first that `free` says isn't taken."""
    n = 1
    name = base
    while not free(name):
        n += 1
        name = f"{base[:60]}-{n}"
    return name


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


def _seal(refresh_token: str | None) -> str | None:
    """A provider's refresh token, encrypted with SOPOPS_SECRET_KEY, the way every other stored credential is.

    Without that key there's nowhere safe to put it, so it isn't kept: signing out then ends the sopops session and
    says so, rather than refusing the sign-in that's happening right now."""
    if not refresh_token:
        return None
    try:
        return crypto.encrypt(refresh_token)
    except crypto.SecretKeyMissing as e:
        log.warning("not keeping the sign-in provider's refresh token (%s), so signing out won't end their session too", e)
        return None


def _unseal(ciphertext: str) -> str | None:
    try:
        return crypto.decrypt(ciphertext)
    except crypto.SecretKeyMissing as e:
        log.warning("can't read the stored refresh token (%s), so only the sopops session ends", e)
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime) -> datetime:
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
