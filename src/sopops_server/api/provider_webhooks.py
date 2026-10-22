"""A login provider's webhooks: `POST /v1/auth/webhooks/{provider}`.

One route for every provider that sends them. It checks the Svix signature on the raw body, stores the delivery
whatever it is (`sopops_server.orm.ProviderEvent`), acts on the few events sopops cares about, and answers. What it
does and why:

**Everything is stored.** A provider lists every event it can send (`LoginProvider.webhook_events`) and sopops
subscribes to all of them, so most deliveries land in the table and nothing else happens. That is the point: the
day an event matters, its history is already here instead of starting from the dashboard visit that turns it on.
The event type is stored as the provider sends it, and an event type it never listed is stored too, with a log line.

**It is idempotent on the message id.** Svix retries, for days, with backoff. The first thing the route does after
the signature is insert the row; the unique `(provider, message_id)` constraint is what makes a retry a duplicate,
and a duplicate is answered 200 without being handled a second time. Nothing here is slow (a handled event is a
couple of statements), so the work happens in the request rather than in a background task that could lose it.

**Nothing but a bad signature gets a non-2xx.** An event with no handler, an event type nobody here knows, a body
that isn't JSON, a storage failure, a handler that raises: all answered 200. A 5xx would buy a retry, and a retry
of a delivery whose storage failed because the database is down is a retry storm aimed at a database that is
already unwell. The cost is that a failure is a log line and, for a handler that raised, a `provider_events` row
with `acted` false and the reason in `note`, to replay by hand. A missing or wrong signature is answered 401 and
nothing is written: it isn't from the provider, and the one thing worth refusing is an unsigned caller writing rows.

A provider this sopops doesn't offer, or one that sends no webhooks, answers 404, and one with no secret set up
answers 503, so a webhook pointed at a deployment that was never configured says so rather than silently accepting
unsigned traffic.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session

from ..auth import webhooks
from ..auth.providers import LoginProvider
from ..orm import ProviderEvent
from ..store import Store

log = logging.getLogger("sopops.auth")

NOTE_MAX = 512


def router(store: Store, actions: webhooks.Actions | None = None) -> APIRouter:
    """`actions` is what the handled events do; it defaults to `webhooks.StoreActions`, which ends login sessions
    over the tables. Hand in `Accounts` (or an adapter) once the methods are there and this route doesn't change."""
    acts: webhooks.Actions = actions if actions is not None else webhooks.StoreActions(store)
    r = APIRouter()

    @r.post("/v1/auth/webhooks/{provider}", tags=["auth"], responses={
        401: {"description": "Unsigned, or the signature doesn't match the endpoint secret."},
        404: {"description": "No such login provider."},
        503: {"description": "No webhook secret is set up for that provider."}})
    async def provider_webhook(provider: str, request: Request) -> dict:
        """A login provider's webhook, signed with the endpoint secret from its dashboard (Svix: `svix-id`,
        `svix-timestamp`, `svix-signature`). Every event is stored; a few end sessions or mark a record stale.
        Answers 2xx for an event nothing acts on, and handles each delivery once."""
        sends = webhooks.sender(provider)
        if sends is None:
            raise HTTPException(404, f"this sopops takes no webhooks from a login provider called '{provider}'")
        endpoint_secret = sends.webhook_secret()
        if not endpoint_secret:
            where = f" ({sends.webhook_secret_env})" if sends.webhook_secret_env else ""
            raise HTTPException(503, f"no webhook secret is set up for {provider}{where}")
        body = await request.body()
        message_id, timestamp, signature = webhooks.headers(request.headers)
        try:
            webhooks.verify(endpoint_secret, body, message_id, timestamp, signature)
        except webhooks.BadSignature as e:
            log.warning("rejected a %s webhook: %s", provider, e)
            raise HTTPException(401, str(e)) from None
        assert message_id is not None  # verify refuses a delivery without one
        return await run_in_threadpool(_receive, store, acts, sends, message_id, body)

    return r


def _payload(body: bytes) -> dict:
    """The body as an object. A body that isn't a JSON object is kept as it arrived rather than refused: it is still
    something the provider sent, and the row is the only record of it."""
    try:
        got = json.loads(body or b"{}")
    except ValueError:
        got = None
    return got if isinstance(got, dict) else {"_unparsed": body.decode("utf-8", "replace")[:20000]}


def _receive(store: Store, acts: webhooks.Actions, sends: LoginProvider, message_id: str, body: bytes) -> dict:
    """Store the delivery and act on it. Never raises: see the module docstring on why this answers 2xx."""
    provider = sends.id
    event = webhooks.normalise(sends, message_id[:128], _payload(body))
    row_id = webhooks.new_id()
    try:
        with Session(store.engine) as db, db.begin():
            db.add(ProviderEvent(id=row_id, provider=provider, event_type=event.type[:128], message_id=event.message_id,
                                 payload=event.payload, received_at=datetime.now(timezone.utc), acted=False,
                                 note=None if event.action else "stored, nothing to do"))
    except IntegrityError:
        log.info("%s webhook %s (%s) arrived again; already handled", provider, event.type, event.message_id)
        return {"ok": True, "event": event.type, "stored": False, "duplicate": True, "acted": False}
    except SQLAlchemyError:
        log.exception("couldn't store a %s webhook (%s); acknowledging it anyway", provider, event.type)
        return {"ok": True, "event": event.type, "stored": False, "duplicate": False, "acted": False}
    if not event.known:
        log.info("%s sent an event sopops doesn't know (%s); stored", provider, event.type)
    if event.action is None:
        return {"ok": True, "event": event.type, "stored": True, "duplicate": False, "acted": False}
    try:
        note, acted = webhooks.act(acts, event) or "", True
    except Exception as e:  # a handler that fails must not buy a retry: the row says what to replay
        log.exception("acting on %s webhook %s (%s) failed", provider, event.type, event.message_id)
        note, acted = f"{event.action} failed: {type(e).__name__}", False
    _note(store, row_id, acted, note)
    return {"ok": True, "event": event.type, "stored": True, "duplicate": False, "acted": acted, "action": event.action}


def _note(store: Store, row_id: str, acted: bool, note: str) -> None:
    try:
        with Session(store.engine) as db, db.begin():
            row = db.get(ProviderEvent, row_id)
            if row is not None:
                row.acted, row.note = acted, note[:NOTE_MAX]
                db.add(row)
    except SQLAlchemyError:
        log.exception("couldn't record what was done with webhook %s", row_id)
