"""Webhooks from the login provider: checking they really came from it, and what sopops does with each one.

sopops subscribes to **every** event PropelAuth can send and stores every one it receives
(`sopops_server.orm.ProviderEvent`), whether or not anything here acts on it. Turning an event on in their dashboard
after the fact only starts the flow from that moment, so the cheap thing is to take them all now and have the
history already here the day one of them matters. `EVENTS` is that list, as data; `HANDLED` is the much shorter list
of the ones that do something, and adding behaviour later is a line in `HANDLED` plus a method on `Actions`.

## The signature (Svix)

PropelAuth sends its webhooks through Svix, and signs them the way Svix signs every message
(https://docs.svix.com/receiving/verifying-payloads/how-manual, linked from
https://docs.propelauth.com/integrations/webhooks). The scheme, which `verify` implements with the standard library
(no `svix` dependency):

- Three headers ride with the request: `svix-id` (the message id), `svix-timestamp` (seconds since the epoch) and
  `svix-signature`. A Svix account on a paid plan may have them renamed with a `webhook-` prefix, so `headers`
  reads either spelling.
- The **signed content** is `{svix-id}.{svix-timestamp}.{body}`, with the body exactly as it arrived: parsing and
  re-serialising the JSON changes the bytes and so changes the signature.
- The **key** is the endpoint secret from their dashboard, `whsec_` followed by base64. The prefix comes off and
  the rest is base64-decoded; those bytes are the HMAC key, not the printable string.
- The **signature** is base64(HMAC-SHA256(key, signed content)).
- `svix-signature` holds one or more space-separated values, each `v1,<base64>` (the version prefix is there so the
  scheme can change, and several appear while a secret is being rotated). The request is signed if any of them
  matches, compared with `hmac.compare_digest` so a wrong signature takes the same time as a right one.
- The timestamp is checked against the clock (`TOLERANCE_S`) and a request outside the window is refused, so a
  body and signature captured off the wire can't be replayed later. Being idempotent on the message id
  (`sopops_server.api.provider_webhooks`) covers the replay inside the window.

## What the events mean here

Vendors rename things. Nothing in sopops switches on `"user.removed_from_org"`: `normalise` turns a delivery into an
`Event` carrying one of the verbs below, and `act` calls the matching method on an `Actions`.

| Verb | Events | Why |
|---|---|---|
| `END_SESSIONS` | `user.disabled`, `user.deleted`, `user.locked` | The important one. A sopops login session lasts 30 days from its last use and doesn't ask the provider anything, so without this a person disabled over there keeps working here for a month. |
| `MEMBERSHIP_CHANGED` | `user.removed_from_org`, `user.added_to_org`, `user.role_changed_within_org` | Their orgs are sopops's teams (`sopops_server.auth.service._mirror_orgs`), mirrored at sign-in; this says the mirror is behind. |
| `RECORD_STALE` | `user.updated`, `org.updated` | These two carry only an id by design ("we only record the user_id", their docs), so there is nothing to copy: the local row is marked stale and whoever cares refetches. |

Everything else is stored and nothing happens.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import update
from sqlmodel import Session, select

from ..orm import Identity, LoginSession

if TYPE_CHECKING:
    from ..store import Store

log = logging.getLogger("sopops.auth")

PROPELAUTH = "propelauth"
PROVIDERS = (PROPELAUTH,)
SECRET_ENV = {PROPELAUTH: "SOPOPS_PROPELAUTH_WEBHOOK_SECRET"}

ID_HEADERS = ("svix-id", "webhook-id")
TIMESTAMP_HEADERS = ("svix-timestamp", "webhook-timestamp")
SIGNATURE_HEADERS = ("svix-signature", "webhook-signature")
VERSION = "v1"  # the only signature version Svix sends today; anything else is ignored rather than trusted
PREFIX = "whsec_"
TOLERANCE_S = 5 * 60  # as Svix's own libraries check: five minutes either way, which covers clock skew and a retry

# --- the events ------------------------------------------------------------------------------------------------

# Every event PropelAuth can send (https://docs.propelauth.com/integrations/webhooks, read 2026-10-22). Subscribe to
# all of them: an event that isn't in `HANDLED` is stored and ignored, which costs a row and no code.
EVENTS: tuple[str, ...] = (
    # organizations
    "org.api_key_deleted", "org.created", "org.deleted", "org.saml_removed", "org.saml_setup", "org.saml_went_live",
    "org.updated", "org.scim_key_created", "org.scim_key_revoked", "org.scim_group_created", "org.scim_group_deleted",
    "org.scim_group_updated",
    # users
    "user.added_to_org", "user.created", "user.deleted", "user.deleted_personal_api_key", "user.disabled",
    "user.enabled", "user.impersonated", "user.invited_to_org", "user.locked", "user.logged_out", "user.login",
    "user.removed_from_org", "user.role_changed_within_org", "user.updated", "user.send_mfa_phone_code",
    "user.added_to_scim_group", "user.removed_from_scim_group",
    # emails (for a project that sends its own)
    "email.account_locked", "email.confirmation_email", "email.merge_account_with_sso", "email.mfa_account_locked",
    "email.org_invite", "email.org_now_requires_2fa", "email.otp_login", "email.passwordless_login",
    "email.reset_password", "email.update_email", "email.waitlist_user_confirmation", "email.waitlist_user_invitation",
)

# sopops's own vocabulary. The core never sees a provider's string.
END_SESSIONS = "sessions.end"
MEMBERSHIP_CHANGED = "membership.changed"
RECORD_STALE = "record.stale"

# The events that do something, and what. Everything else is stored and ignored; giving an event behaviour is a line
# here and a method on `Actions`.
HANDLED: dict[str, str] = {
    "user.disabled": END_SESSIONS,
    "user.deleted": END_SESSIONS,
    "user.locked": END_SESSIONS,
    "user.removed_from_org": MEMBERSHIP_CHANGED,
    "user.added_to_org": MEMBERSHIP_CHANGED,
    "user.role_changed_within_org": MEMBERSHIP_CHANGED,
    "user.updated": RECORD_STALE,
    "org.updated": RECORD_STALE,
}


class BadSignature(Exception):
    """A delivery that didn't verify: headers missing, a timestamp outside the window, or a signature that doesn't
    match the endpoint secret. The message is safe to show and never quotes the signature or the secret."""


# --- the signature ---------------------------------------------------------------------------------------------


def secret(provider: str) -> str | None:
    """The endpoint secret for this provider's webhooks, from the environment. None when none is set up."""
    env = SECRET_ENV.get(provider)
    return (os.environ.get(env, "").strip() or None) if env else None


def headers(sent: Any) -> tuple[str | None, str | None, str | None]:
    """The (id, timestamp, signature) headers of a delivery, under either spelling Svix uses."""

    def first(names: tuple[str, ...]) -> str | None:
        for name in names:
            got = sent.get(name)
            if got:
                return got
        return None

    return first(ID_HEADERS), first(TIMESTAMP_HEADERS), first(SIGNATURE_HEADERS)


def signing_key(endpoint_secret: str) -> bytes:
    """The HMAC key in the endpoint secret: `whsec_` off the front, the rest base64-decoded. Raises BadSignature for
    a secret this server can't use, because a request can't be judged without one."""
    raw = endpoint_secret.strip()
    raw = raw[len(PREFIX):] if raw.startswith(PREFIX) else raw
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise BadSignature("the webhook secret set up here isn't base64 after its whsec_ prefix") from None


def sign(endpoint_secret: str, body: bytes, message_id: str, timestamp: str) -> str:
    """The signature Svix would send for this delivery, base64, without its `v1,` prefix."""
    signed = message_id.encode() + b"." + timestamp.encode() + b"." + body
    return base64.b64encode(hmac.new(signing_key(endpoint_secret), signed, hashlib.sha256).digest()).decode()


def verify(endpoint_secret: str, body: bytes, message_id: str | None, timestamp: str | None, signature: str | None,
           now: float | None = None, tolerance_s: int = TOLERANCE_S) -> None:
    """Check a delivery against the endpoint secret, as the module docstring sets out. Returns None; raises
    BadSignature. `body` is the raw bytes of the request, never a re-serialised payload."""
    if not (message_id and timestamp and signature):
        raise BadSignature("the webhook didn't carry the signature headers")
    try:
        sent_at = int(timestamp.strip())
    except ValueError:
        raise BadSignature("the webhook's timestamp isn't a number of seconds") from None
    if abs((time.time() if now is None else now) - sent_at) > tolerance_s:
        raise BadSignature("the webhook's timestamp is too far from this server's clock")
    want = sign(endpoint_secret, body, message_id, timestamp)
    ok = False
    for part in signature.split(" "):
        version, _, value = part.strip().partition(",")
        if version != VERSION or not value:
            continue  # a version this doesn't know is ignored, never taken on trust
        ok = hmac.compare_digest(value, want) or ok  # every value is compared, so the time doesn't say which matched
    if not ok:
        raise BadSignature("the webhook's signature doesn't match the endpoint secret")


# --- normalising -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """One delivery, in sopops's terms. `action` is a verb above, or None for the ones nothing acts on."""

    provider: str
    type: str
    message_id: str
    payload: dict[str, Any] = field(repr=False)
    action: str | None = None
    user_id: str | None = None  # the provider's user id, not sopops's
    org_id: str | None = None  # the provider's org id, not sopops's
    known: bool = True  # False for an event type this sopops has never heard of: stored, ignored, worth a log line


def _id(payload: dict[str, Any], key: str, *under: str) -> str | None:
    """An id from the payload: `user_id` at the top, or inside the object they sometimes nest it in."""
    got = payload.get(key)
    if isinstance(got, (str, int)) and str(got).strip():
        return str(got).strip()
    for name in under:
        inner = payload.get(name)
        if isinstance(inner, dict):
            for k in (key, "id"):
                deep = inner.get(k)
                if isinstance(deep, (str, int)) and str(deep).strip():
                    return str(deep).strip()
    return None


def normalise(provider: str, message_id: str, payload: dict[str, Any]) -> Event:
    """A delivery as an `Event`. The type is their `event_type` (`event` and `type` are read too, so a rename or
    another provider doesn't arrive as an empty string); an unknown one is kept exactly as sent and acts on
    nothing."""
    kind = ""
    for key in ("event_type", "event", "type"):
        got = payload.get(key)
        if isinstance(got, str) and got.strip():
            kind = got.strip()
            break
    return Event(provider=provider, type=kind, message_id=message_id, payload=payload, action=HANDLED.get(kind),
                 user_id=_id(payload, "user_id", "user"), org_id=_id(payload, "org_id", "org"), known=kind in EVENTS)


# --- what happens ----------------------------------------------------------------------------------------------


class Actions(Protocol):
    """What the core does about an event. The webhook route knows this and nothing else, so the handling can move
    into `sopops_server.auth.service` without the route changing. Each method returns a short note for the stored
    row (`provider_events.note`), and may raise: the route logs it and still answers 2xx."""

    def end_sessions(self, provider: str, provider_user_id: str | None) -> str:
        """This person can't sign in over there any more: end their sopops login sessions now."""
        ...

    def membership_changed(self, provider: str, provider_user_id: str | None, provider_org_id: str | None) -> str:
        """They joined, left, or changed role in one of the provider's orgs."""
        ...

    def record_stale(self, provider: str, kind: str, provider_id: str | None) -> str:
        """The provider changed a user or an org and told us only its id: what's held here is out of date."""
        ...


class StoreActions:
    """The default `Actions`, over the tables alone, so the route works before anything is wired into
    `sopops_server.auth.service`.

    `end_sessions` is real and is the one that matters: the `identities` row for the provider's user id gives the
    sopops user, and every live login session of theirs is revoked. It deliberately does the same thing
    `Accounts._end_sessions` does for a password change, and nothing more: the provider's own session is already
    gone (that is what the event says), and the account itself is left alone, because a webhook is not the place to
    delete somebody's data.

    The other two only write a note. A membership and a stale name or email are both put right by
    `Accounts._mirror_orgs` at the person's next sign-in, so the cost of not acting is an out-of-date team list for
    one person, not access they shouldn't have. Wiring them to act at once is a method on `Accounts`.
    """

    def __init__(self, store: Store):
        self.store = store

    def end_sessions(self, provider: str, provider_user_id: str | None) -> str:
        if not provider_user_id:
            return "the event didn't say which user"
        with Session(self.store.engine) as db, db.begin():
            users = list(db.exec(select(Identity.user_id).where(Identity.provider == provider,  # type: ignore[arg-type]
                                                                Identity.provider_user_id == provider_user_id)))
            if not users:
                return "nobody here has signed in with that account"
            done = db.execute(update(LoginSession)
                              .where(LoginSession.user_id.in_(users), LoginSession.revoked_at.is_(None))  # type: ignore[union-attr, attr-defined]
                              .values(revoked_at=datetime.now(timezone.utc)))
        log.info("%s says a user can't sign in any more: ended %s login session(s) here", provider, done.rowcount)
        return f"ended {done.rowcount} login session(s)"

    def membership_changed(self, provider: str, provider_user_id: str | None, provider_org_id: str | None) -> str:
        return "noted; the teams are mirrored again at their next sign-in"

    def record_stale(self, provider: str, kind: str, provider_id: str | None) -> str:
        return f"noted; the {kind} is refetched at the next sign-in"


def act(actions: Actions, event: Event) -> str | None:
    """Do whatever this event asks for, and return the note to store. None means nothing was asked of us."""
    if event.action == END_SESSIONS:
        return actions.end_sessions(event.provider, event.user_id)
    if event.action == MEMBERSHIP_CHANGED:
        return actions.membership_changed(event.provider, event.user_id, event.org_id)
    if event.action == RECORD_STALE:
        return actions.record_stale(event.provider, "org" if event.type.startswith("org.") else "user",
                                    event.org_id if event.type.startswith("org.") else event.user_id)
    return None


def new_id() -> str:
    return str(uuid.uuid4())
