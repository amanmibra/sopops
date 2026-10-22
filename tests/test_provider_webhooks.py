"""A login provider's webhooks, signed the way Svix signs.

A provider says which events it can send and which of them mean something here; sopops stores every delivery and
acts on the few that are mapped. The provider in this file is a made-up one, registered for the test, because the
route is sopops' and knows nothing about any vendor's event names.

What's worth pinning down is the perimeter and the promises: the signature is checked the way Svix signs
(including their own published test vector, so this file would catch an implementation that only agrees with
itself), an old timestamp and a wrong signature write nothing, a retry of a delivery is handled once, an event the
provider never listed is stored and ignored, and the provider's "this person is gone" event ends that person's
sopops sessions, the one that matters, because a login session otherwise lives for 30 days without asking the
provider anything.

The route is mounted on a bare app here: how it's hung on `sopops_server.app` is tested in tests/test_auth.py.
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
from sopops_server.auth import providers, webhooks
from sopops_server.auth.providers import LoginProvider
from sopops_server.orm import Identity, LoginSession, ProviderEvent, User

SECRET = "whsec_" + base64.b64encode(b"sopops-provider-webhook-test-key").decode()
SECRET_ENV = "SOPOPS_OCTO_WEBHOOK_SECRET"
THEIR_USER = "octo_user_7f3c"
THEIR_TEAM = "octo_team_1"


class Octo(LoginProvider):
    """A login provider that sends webhooks, as a plugin's would: its own event names, its own endpoint secret,
    and a mapping of the ones that mean something onto sopops' verbs. The core knows none of these strings."""

    id = "octo"
    label = "Octo"
    webhook_secret_env = SECRET_ENV
    webhook_events = ("person.gone", "person.joined_team", "person.renamed", "team.renamed", "person.signed_in")
    webhook_actions = {"person.gone": webhooks.END_SESSIONS, "person.joined_team": webhooks.MEMBERSHIP_CHANGED,
                       "person.renamed": webhooks.RECORD_STALE, "team.renamed": webhooks.RECORD_STALE}

    def configured(self) -> bool:
        return True


class Quiet(LoginProvider):
    """One that signs people in and sends nothing. Its webhook path is closed."""

    id = "quiet"
    label = "Quiet"

    def configured(self) -> bool:
        return True


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


@pytest.fixture(autouse=True)
def octo(monkeypatch):
    """The provider, registered for the test and taken away again."""
    monkeypatch.setenv(SECRET_ENV, SECRET)
    providers.register(Octo())
    providers.register(Quiet())
    yield
    providers.unregister("octo")
    providers.unregister("quiet")


@pytest.fixture
def store(tmp_path):
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
         signature: str | None = None, provider: str = "octo"):
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
    body, msg_id, stamp = b'{"event_type":"person.signed_in"}', "msg_1", str(int(time.time()))
    good = webhooks.sign(SECRET, body, msg_id, stamp)
    webhooks.verify(SECRET, body, msg_id, stamp, f"v1,{base64.b64encode(b'nope').decode()} v1,{good}")
    with pytest.raises(webhooks.BadSignature):
        webhooks.verify(SECRET, body, msg_id, stamp, f"v9,{good}")


def test_valid_signature_is_accepted_and_stored(client, store):
    got = post(client, {"event_type": "person.signed_in", "user_id": THEIR_USER})
    assert got.status_code == 200, got.text
    assert got.json()["stored"] is True and got.json()["acted"] is False
    (row,) = rows(store)
    assert (row.provider, row.event_type, row.acted) == ("octo", "person.signed_in", False)
    assert row.payload == {"event_type": "person.signed_in", "user_id": THEIR_USER}


def test_bad_signature_is_refused_and_writes_nothing(client, store):
    body_signed_with_another_secret = post(client, {"event_type": "person.gone", "user_id": THEIR_USER},
                                           secret="whsec_" + base64.b64encode(b"a different endpoint secret!!").decode())
    assert body_signed_with_another_secret.status_code == 401
    assert post(client, {"event_type": "person.signed_in"}, signature="v1,bm90YXNpZ25hdHVyZQ==").status_code == 401
    assert rows(store) == []


def test_old_timestamp_is_refused(client, store):
    """A body and signature captured off the wire can't be posted back an hour later."""
    assert post(client, {"event_type": "person.signed_in"}, at=int(time.time()) - 3600).status_code == 401
    assert post(client, {"event_type": "person.signed_in"}, at=int(time.time()) + 3600).status_code == 401
    assert rows(store) == []


def test_unsigned_unknown_or_unconfigured(client, store, monkeypatch):
    """A provider nobody registered and one that sends no webhooks are both 404; a provider with no secret set up
    here is 503, so a webhook aimed at the wrong deployment says so instead of taking unsigned traffic."""
    assert client.post("/v1/auth/webhooks/octo", json={"event_type": "person.signed_in"}).status_code == 401
    assert post(client, {"event_type": "person.signed_in"}, provider="octopus").status_code == 404
    assert post(client, {"event_type": "person.signed_in"}, provider="quiet").status_code == 404
    monkeypatch.delenv(SECRET_ENV)
    assert post(client, {"event_type": "person.signed_in"}).status_code == 503
    assert rows(store) == []


def test_a_provider_this_deployment_doesnt_offer_takes_no_webhooks(client, store, monkeypatch):
    """SOPOPS_LOGIN_PROVIDERS is the whole answer to which providers this sopops has anything to do with."""
    monkeypatch.setenv(providers.ALLOWLIST_ENV, "quiet")
    assert post(client, {"event_type": "person.gone", "user_id": THEIR_USER}).status_code == 404
    assert rows(store) == []


# --- storing -----------------------------------------------------------------------------------------------------


def test_a_retry_is_stored_once_and_handled_once(client, store, acts):
    """Svix retries for days; the message id is what makes that harmless."""
    first = post(client, {"event_type": "person.gone", "user_id": THEIR_USER}, msg_id="msg_same")
    again = post(client, {"event_type": "person.gone", "user_id": THEIR_USER}, msg_id="msg_same")
    assert (first.status_code, again.status_code) == (200, 200)
    assert first.json()["duplicate"] is False and again.json()["duplicate"] is True
    assert len(rows(store)) == 1
    assert [c[0] for c in acts.calls] == ["end_sessions"]


def test_an_unknown_event_is_stored_and_ignored(client, store, acts):
    got = post(client, {"event_type": "person.teleported", "user_id": THEIR_USER})
    assert got.status_code == 200 and got.json()["acted"] is False
    (row,) = rows(store)
    assert row.event_type == "person.teleported" and row.note == "stored, nothing to do"
    assert acts.calls == []


def test_every_event_the_provider_lists_is_taken(client, store):
    """Whatever it says it can send, the ones with no behaviour included: that's what makes subscribing to all of
    them at the provider cost a row and no code."""
    for name in Octo.webhook_events:
        assert post(client, {"event_type": name, "user_id": THEIR_USER, "org_id": THEIR_TEAM}).status_code == 200
    assert {r.event_type for r in rows(store)} == set(Octo.webhook_events)


def test_a_body_that_isnt_json_is_still_kept(client, store):
    msg_id, stamp = "msg_raw", str(int(time.time()))
    sig = "v1," + webhooks.sign(SECRET, b"not json", msg_id, stamp)
    got = client.post("/v1/auth/webhooks/octo", content=b"not json",
                      headers={"svix-id": msg_id, "svix-timestamp": stamp, "svix-signature": sig})
    assert got.status_code == 200
    (row,) = rows(store)
    assert row.payload["_unparsed"] == "not json" and row.event_type == ""


# --- what sopops does about them ---------------------------------------------------------------------------------


def test_a_person_who_is_gone_there_loses_their_sessions_here(client, store, acts):
    got = post(client, {"event_type": "person.gone", "user_id": THEIR_USER})
    assert got.status_code == 200 and got.json()["acted"] is True
    assert acts.calls == [("end_sessions", "octo", THEIR_USER)]
    (row,) = rows(store)
    assert row.acted is True and row.note == "ended them"


def test_membership_and_stale_events(client, acts):
    """Which record went stale is read off the ids the event carries, not off the provider's name for it."""
    post(client, {"event_type": "person.joined_team", "user_id": THEIR_USER, "org_id": THEIR_TEAM})
    post(client, {"event_type": "person.renamed", "user_id": THEIR_USER})
    post(client, {"event_type": "team.renamed", "org_id": THEIR_TEAM})
    assert acts.calls == [("membership_changed", "octo", THEIR_USER, THEIR_TEAM),
                          ("record_stale", "octo", "user", THEIR_USER),
                          ("record_stale", "octo", "org", THEIR_TEAM)]


def test_a_handler_that_fails_still_answers_2xx(client, store, acts):
    """A 5xx would buy a retry; the row keeps the reason instead, to replay by hand."""

    def boom(*a, **kw):
        raise RuntimeError("the database went away")

    acts.end_sessions = boom  # type: ignore[method-assign]
    got = post(client, {"event_type": "person.gone", "user_id": THEIR_USER})
    assert got.status_code == 200 and got.json()["acted"] is False
    (row,) = rows(store)
    assert row.acted is False and row.note == "sessions.end failed: RuntimeError"


def test_the_default_actions_really_end_the_sessions(store):
    """`StoreActions` is what runs until something is wired into `auth.service`: it has to actually revoke."""
    now = datetime.now(timezone.utc)
    with Session(store.engine) as db, db.begin():
        db.add(User(id="u1", email="jo@acme.test", name="Jo", created_at=now))
        db.flush()
        db.add(Identity(id="i1", user_id="u1", provider="octo", provider_user_id=THEIR_USER, email="jo@acme.test", created_at=now))
        for n in (1, 2):
            db.add(LoginSession(id=f"s{n}", token_hash=f"hash{n}", user_id="u1", created_at=now,
                                expires_at=now + timedelta(days=30), last_seen_at=now))
    app = FastAPI()
    app.include_router(provider_webhooks.router(store))
    got = post(TestClient(app), {"event_type": "person.gone", "user_id": THEIR_USER})
    assert got.status_code == 200 and got.json()["acted"] is True
    with Session(store.engine) as db:
        assert all(s.revoked_at is not None for s in db.exec(select(LoginSession)))
    (row,) = rows(store)
    assert row.note == "ended 2 login session(s)"


def test_the_default_actions_shrug_at_a_stranger(store):
    """An event about somebody who never signed in here is not an error."""
    assert webhooks.StoreActions(store).end_sessions("octo", "octo_user_nobody") == "nobody here has signed in with that account"
    assert webhooks.StoreActions(store).end_sessions("octo", None) == "the event didn't say which user"
