"""The login provider's webhooks: PropelAuth's, signed by Svix.

sopops takes every event PropelAuth can send and stores all of them; a handful do something. What's worth pinning
down is the perimeter and the promises: the signature is checked the way Svix signs (including their own published
test vector, so this file would catch an implementation that only agrees with itself), an old timestamp and a wrong
signature write nothing, a retry of a delivery is handled once, an event nobody here has heard of is stored and
ignored, and `user.disabled` ends that person's sopops sessions — the one event that matters, because a login
session otherwise lives for 30 days without asking the provider anything.

The route is mounted on a bare app here: `sopops_server.app` and the route table in `sopops_server.auth.access` are
somebody else's edit, and these tests are about the route, not about how it's hung on the app.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from conftest import SOPC_BIN, fresh_db_url
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from sopops_server import Sopc, Store
from sopops_server.api import provider_webhooks
from sopops_server.auth import webhooks
from sopops_server.orm import Identity, LoginSession, ProviderEvent, User

SECRET = "whsec_" + base64.b64encode(b"sopops-provider-webhook-test-key").decode()
PROPEL_USER = "pa_user_7f3c"


class Recorder:
    """An `Actions` that writes down what it was asked to do, instead of doing it."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def end_sessions(self, provider: str, provider_user_id: str | None) -> str:
        self.calls.append(("end_sessions", provider, provider_user_id))
        return "ended them"

    def membership_changed(self, provider: str, provider_user_id: str | None, provider_org_id: str | None) -> str:
        self.calls.append(("membership_changed", provider, provider_user_id, provider_org_id))
        return "noted"

    def record_stale(self, provider: str, kind: str, provider_id: str | None) -> str:
        self.calls.append(("record_stale", provider, kind, provider_id))
        return "noted"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SOPOPS_PROPELAUTH_WEBHOOK_SECRET", SECRET)
    return Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN))


@pytest.fixture
def acts():
    return Recorder()


@pytest.fixture
def client(store, acts):
    app = FastAPI()
    app.include_router(provider_webhooks.router(store, acts))
    return TestClient(app)


def rows(store) -> list[ProviderEvent]:
    with Session(store.engine, expire_on_commit=False) as db:
        return list(db.exec(select(ProviderEvent).order_by(ProviderEvent.received_at)))  # type: ignore[arg-type]


def post(client, payload: dict, *, msg_id: str | None = None, at: int | None = None, secret: str = SECRET,
         signature: str | None = None, provider: str = "propelauth"):
    """Sign a body the way Svix does and post it."""
    body = json.dumps(payload).encode()
    msg_id = msg_id or f"msg_{uuid.uuid4().hex[:12]}"
    stamp = str(int(time.time()) if at is None else at)
    sig = signature if signature is not None else "v1," + webhooks.sign(secret, body, msg_id, stamp)
    return client.post(f"/v1/auth/webhooks/{provider}", content=body, headers={
        "content-type": "application/json", "svix-id": msg_id, "svix-timestamp": stamp, "svix-signature": sig})


# --- the signature -----------------------------------------------------------------------------------------------


def test_svix_test_vector():
    """Svix publishes a secret, body, id, timestamp and signature; ours has to agree with theirs, not just itself."""
    secret, body = "whsec_plJ3nmyCDGBKInavdOK15jsl", b'{"event_type":"ping","data":{"success":true}}'
    msg_id, stamp = "msg_loFOjxBNrRLzqYUf", "1731705121"
    assert webhooks.sign(secret, body, msg_id, stamp) == "rAvfW3dJ/X/qxhsaXPOyyCGmRKsaKWcsNccKXlIktD0="
    webhooks.verify(secret, body, msg_id, stamp, "v1,rAvfW3dJ/X/qxhsaXPOyyCGmRKsaKWcsNccKXlIktD0=", now=int(stamp))
    with pytest.raises(webhooks.BadSignature):  # the vector's timestamp is years old: tolerance is checked too
        webhooks.verify(secret, body, msg_id, stamp, "v1,rAvfW3dJ/X/qxhsaXPOyyCGmRKsaKWcsNccKXlIktD0=")


def test_several_signatures_and_versions():
    """The header can hold several space-separated values (a secret mid-rotation); any `v1` match is enough, and a
    version this doesn't know is never taken on trust."""
    body, msg_id, stamp = b'{"event_type":"user.login"}', "msg_1", str(int(time.time()))
    good = webhooks.sign(SECRET, body, msg_id, stamp)
    webhooks.verify(SECRET, body, msg_id, stamp, f"v1,{base64.b64encode(b'nope').decode()} v1,{good}")
    with pytest.raises(webhooks.BadSignature):
        webhooks.verify(SECRET, body, msg_id, stamp, f"v9,{good}")


def test_valid_signature_is_accepted_and_stored(client, store):
    got = post(client, {"event_type": "user.login", "user_id": PROPEL_USER})
    assert got.status_code == 200, got.text
    assert got.json()["stored"] is True and got.json()["acted"] is False
    (row,) = rows(store)
    assert (row.provider, row.event_type, row.acted) == ("propelauth", "user.login", False)
    assert row.payload == {"event_type": "user.login", "user_id": PROPEL_USER}


def test_bad_signature_is_refused_and_writes_nothing(client, store):
    body_signed_with_another_secret = post(client, {"event_type": "user.disabled", "user_id": PROPEL_USER},
                                           secret="whsec_" + base64.b64encode(b"a different endpoint secret!!").decode())
    assert body_signed_with_another_secret.status_code == 401
    assert post(client, {"event_type": "user.login"}, signature="v1,bm90YXNpZ25hdHVyZQ==").status_code == 401
    assert rows(store) == []


def test_old_timestamp_is_refused(client, store):
    """A body and signature captured off the wire can't be posted back an hour later."""
    assert post(client, {"event_type": "user.login"}, at=int(time.time()) - 3600).status_code == 401
    assert post(client, {"event_type": "user.login"}, at=int(time.time()) + 3600).status_code == 401
    assert rows(store) == []


def test_unsigned_or_unconfigured(client, store, monkeypatch):
    assert client.post("/v1/auth/webhooks/propelauth", json={"event_type": "user.login"}).status_code == 401
    assert post(client, {"event_type": "user.login"}, provider="octopus").status_code == 404
    monkeypatch.delenv("SOPOPS_PROPELAUTH_WEBHOOK_SECRET")
    assert post(client, {"event_type": "user.login"}).status_code == 503
    assert rows(store) == []


# --- storing -----------------------------------------------------------------------------------------------------


def test_a_retry_is_stored_once_and_handled_once(client, store, acts):
    """Svix retries for days; the message id is what makes that harmless."""
    first = post(client, {"event_type": "user.disabled", "user_id": PROPEL_USER}, msg_id="msg_same")
    again = post(client, {"event_type": "user.disabled", "user_id": PROPEL_USER}, msg_id="msg_same")
    assert (first.status_code, again.status_code) == (200, 200)
    assert first.json()["duplicate"] is False and again.json()["duplicate"] is True
    assert len(rows(store)) == 1
    assert [c[0] for c in acts.calls] == ["end_sessions"]


def test_an_unknown_event_is_stored_and_ignored(client, store, acts):
    got = post(client, {"event_type": "user.teleported", "user_id": PROPEL_USER})
    assert got.status_code == 200 and got.json()["acted"] is False
    (row,) = rows(store)
    assert row.event_type == "user.teleported" and row.note == "stored, nothing to do"
    assert acts.calls == []


def test_every_event_they_can_send_is_taken(client, store):
    """All 41 of them, and the ones with no behaviour are stored just the same."""
    assert len(webhooks.EVENTS) == 41
    for name in webhooks.EVENTS:
        assert post(client, {"event_type": name, "user_id": PROPEL_USER, "org_id": "pa_org_1"}).status_code == 200
    assert {r.event_type for r in rows(store)} == set(webhooks.EVENTS)


def test_a_body_that_isnt_json_is_still_kept(client, store):
    msg_id, stamp = "msg_raw", str(int(time.time()))
    sig = "v1," + webhooks.sign(SECRET, b"not json", msg_id, stamp)
    got = client.post("/v1/auth/webhooks/propelauth", content=b"not json",
                      headers={"svix-id": msg_id, "svix-timestamp": stamp, "svix-signature": sig})
    assert got.status_code == 200
    (row,) = rows(store)
    assert row.payload["_unparsed"] == "not json" and row.event_type == ""


# --- what sopops does about them ---------------------------------------------------------------------------------


@pytest.mark.parametrize("event", ["user.disabled", "user.deleted", "user.locked"])
def test_a_disabled_person_loses_their_sessions(client, store, acts, event):
    got = post(client, {"event_type": event, "user_id": PROPEL_USER})
    assert got.status_code == 200 and got.json()["acted"] is True
    assert acts.calls == [("end_sessions", "propelauth", PROPEL_USER)]
    (row,) = rows(store)
    assert row.acted is True and row.note == "ended them"


def test_membership_and_stale_events(client, acts):
    post(client, {"event_type": "user.removed_from_org", "user_id": PROPEL_USER, "org_id": "pa_org_1"})
    post(client, {"event_type": "user.updated", "user_id": PROPEL_USER})
    post(client, {"event_type": "org.updated", "org_id": "pa_org_1"})
    assert acts.calls == [("membership_changed", "propelauth", PROPEL_USER, "pa_org_1"),
                          ("record_stale", "propelauth", "user", PROPEL_USER),
                          ("record_stale", "propelauth", "org", "pa_org_1")]


def test_a_handler_that_fails_still_answers_2xx(client, store, acts):
    """A 5xx would buy a retry; the row keeps the reason instead, to replay by hand."""

    def boom(*a, **kw):
        raise RuntimeError("the database went away")

    acts.end_sessions = boom  # type: ignore[method-assign]
    got = post(client, {"event_type": "user.disabled", "user_id": PROPEL_USER})
    assert got.status_code == 200 and got.json()["acted"] is False
    (row,) = rows(store)
    assert row.acted is False and row.note == "sessions.end failed: RuntimeError"


def test_the_default_actions_really_end_the_sessions(store, tmp_path):
    """`StoreActions` is what runs until something is wired into `auth.service`: it has to actually revoke."""
    now = datetime.now(timezone.utc)
    with Session(store.engine) as db, db.begin():
        db.add(User(id="u1", email="jo@acme.test", name="Jo", created_at=now))
        db.flush()
        db.add(Identity(id="i1", user_id="u1", provider="propelauth", provider_user_id=PROPEL_USER, email="jo@acme.test", created_at=now))
        for n in (1, 2):
            db.add(LoginSession(id=f"s{n}", token_hash=f"hash{n}", user_id="u1", created_at=now,
                                expires_at=now + timedelta(days=30), last_seen_at=now))
    app = FastAPI()
    app.include_router(provider_webhooks.router(store))
    got = post(TestClient(app), {"event_type": "user.disabled", "user_id": PROPEL_USER})
    assert got.status_code == 200 and got.json()["acted"] is True
    with Session(store.engine) as db:
        assert all(s.revoked_at is not None for s in db.exec(select(LoginSession)))
    (row,) = rows(store)
    assert row.note == "ended 2 login session(s)"


def test_the_default_actions_shrug_at_a_stranger(store):
    """An event about somebody who never signed in here is not an error."""
    assert webhooks.StoreActions(store).end_sessions("propelauth", "pa_user_nobody") == "nobody here has signed in with that account"
    assert webhooks.StoreActions(store).end_sessions("propelauth", None) == "the event didn't say which user"
