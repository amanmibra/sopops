"""Sessions: canonical ingest, the ingest token, listing, the import CLI, live events and the inline pipeline."""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from conftest import WS
from sopops.cli import main as cli
from sopops.events import Broker
from sopops.pipeline.normalize import normalize_turns
from sopops.pipeline.process import NO_MODEL
from sopops.schemas import SessionIn

T0 = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)


def session(external_id="room-1", **kw):
    t0 = datetime.fromisoformat(kw["started_at"]).timestamp() if kw.get("started_at") else T0.timestamp()
    return {
        "platform": "livekit",
        "external_id": external_id,
        "agent": "tonys-pizza",
        "started_at": T0.isoformat(),
        "ended_at": (T0 + timedelta(seconds=95)).isoformat(),
        "turns": [
            {"role": "assistant", "text": " Tony's Pizza, how can I help? ", "at": t0 + 1},
            {"role": "user", "text": "A large pepperoni for delivery.", "at": t0 + 4.5},
            {"role": "user", "text": "A large pepperoni for delivery.", "at": t0 + 4.6},  # LiveKit repeats items
            {"role": "function", "text": "", "name": "check_delivery_zone", "at": t0 + 9},
            {"role": "agent", "text": "", "at": t0 + 10},
        ],
        "tool_calls": [{"name": "check_delivery_zone", "arguments": {"zip": "10001"}, "result": {"ok": True}, "offset_s": 9}],
        "metadata": {"room": "room-1"},
        **kw,
    }


@pytest.fixture
def token(client):
    return client.post(f"{WS}/tokens").json()["token"]


def ingest(client, token, body):
    return client.post(f"{WS}/sessions", json=body, headers={"Authorization": f"Bearer {token}"})


def test_ingest_needs_a_token_with_the_ingest_scope(client):
    assert client.post(f"{WS}/sessions", json=session()).status_code == 401
    assert client.get(f"{WS}/tokens").json() == []

    first = client.post(f"{WS}/tokens", json={"name": "LiveKit", "scopes": ["ingest"]}).json()
    assert first["token"].startswith("sopops_") and first["token"].startswith(first["prefix"])
    assert first["scopes"] == ["ingest"] and first["active"] and first["expires_at"] is None
    [info] = client.get(f"{WS}/tokens").json()
    assert info["id"] == first["id"] and info["name"] == "LiveKit" and "token" not in info
    assert ingest(client, "nope", session()).status_code == 401
    assert ingest(client, first["token"], session()).status_code == 201

    runtime_only = client.post(f"{WS}/tokens", json={"scopes": ["runtime"]}).json()["token"]
    assert ingest(client, runtime_only, session("room-2")).status_code == 403
    second = client.post(f"{WS}/tokens").json()  # default scopes: ingest and runtime
    assert second["scopes"] == ["ingest", "runtime"]
    assert ingest(client, second["token"], session("room-2")).status_code == 201
    assert client.post("/v1/workspaces/other/sessions", json=session(), headers={"Authorization": f"Bearer {second['token']}"}).status_code == 401

    revoked = client.delete(f"{WS}/tokens/{second['id']}").json()
    assert revoked["revoked_at"] and not revoked["active"]
    assert ingest(client, second["token"], session("room-3")).status_code == 401
    assert ingest(client, first["token"], session("room-3")).status_code == 201  # the others keep working
    assert client.delete(f"{WS}/tokens/nope").status_code == 404
    assert client.post(f"{WS}/tokens", json={"scopes": ["onboarding:write"]}).status_code == 422  # only imports make those
    assert client.post(f"{WS}/tokens", json={"scopes": []}).status_code == 422


def test_an_expired_token_stops_working(client):
    from datetime import datetime, timedelta, timezone

    from sqlmodel import Session

    from sopops.orm import Token

    made = client.post(f"{WS}/tokens", json={"expires_in_days": 1}).json()
    assert made["expires_at"] and ingest(client, made["token"], session()).status_code == 201
    store = client.app.state.store
    with Session(store.engine) as db:
        t = db.get(Token, made["id"])
        t.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.add(t)
        db.commit()
    res = ingest(client, made["token"], session("room-2"))
    assert res.status_code == 401 and "expired" in res.json()["detail"]


def test_ingest_is_an_idempotent_upsert(client, token):
    res = ingest(client, token, session())
    assert res.status_code == 201
    created = res.json()
    assert created["created"] and not created["updated"] and created["status"] == "received"

    again = ingest(client, token, session())  # a retried webhook
    assert again.status_code == 200 and again.json() == {**created, "created": False, "updated": False}

    changed = ingest(client, token, session(metadata={"room": "room-1", "late": True}))
    assert changed.status_code == 200 and changed.json()["id"] == created["id"]
    assert changed.json()["updated"] and not changed.json()["created"]
    assert client.get(f"{WS}/sessions").json()["items"][0]["id"] == created["id"]
    assert len(client.get(f"{WS}/sessions").json()["items"]) == 1

    # The key is (platform, external_id): the same id on another platform is another session.
    other = ingest(client, token, session(platform="vapi"))
    assert other.status_code == 201 and other.json()["id"] != created["id"]


def test_the_unique_key_holds_in_the_database(client):
    from sqlalchemy.exc import IntegrityError
    from sqlmodel import Session as Db

    from sopops.orm import SessionRecord

    store = client.app.state.store
    store.sessions.ingest("demo", SessionIn(**session()))
    row = dict(
        workspace_id="demo", channel="voice", platform="livekit", external_id="room-1", started_at=T0, status="received",
        turns=[], tool_calls=[], meta={}, payload_hash="x", created_at=T0, updated_at=T0,
    )
    with pytest.raises(IntegrityError), Db(store.engine) as db:
        db.add(SessionRecord(id="dup", **row))
        db.commit()


def test_inline_pipeline_normalizes_turns_and_sets_status(client, token):
    sid = ingest(client, token, session()).json()["id"]
    s = client.get(f"{WS}/sessions/{sid}").json()  # TestClient runs background tasks before returning
    assert s["processed_at"] and s["status"] == "received" and s["status_reason"] == NO_MODEL
    assert [(t["role"], t["text"], t["offset_s"]) for t in s["turns"]] == [
        ("agent", "Tony's Pizza, how can I help?", 1.0),
        ("user", "A large pepperoni for delivery.", 4.5),
        ("tool", "", 9.0),
    ]
    assert s["turns"][2]["name"] == "check_delivery_zone" and "at" not in s["turns"][0]
    assert s["duration_s"] == 95 and s["tool_call_count"] == 1 and s["metadata"] == {"room": "room-1"}
    late = {**session("room-late", tool_calls=[{"name": "f", "at": T0.timestamp() + 9}]), "started_at": (T0 + timedelta(seconds=3)).isoformat()}
    s = client.get(f"{WS}/sessions/{ingest(client, token, late).json()['id']}").json()
    assert s["started_at"] == "2026-10-01T18:00:01Z"  # the first turn, not the late start the sender noted
    assert s["turns"][0]["offset_s"] == 0 and s["tool_calls"][0]["offset_s"] == 8 and "at" not in s["tool_calls"][0]

    empty = ingest(client, token, session("room-empty", turns=[])).json()["id"]
    assert client.get(f"{WS}/sessions/{empty}").json()["status"] == "partial"
    flagged = ingest(client, token, session("room-cut", partial=True)).json()
    assert flagged["status"] == "partial"
    assert client.get(f"{WS}/sessions/{flagged['id']}").json()["status_reason"] == "The sender marked the transcript incomplete."


def test_normalize_is_idempotent_and_reads_seconds_or_milliseconds():
    turns = [
        {"role": "bot", "text": "Hi", "at": (T0.timestamp() + 2) * 1000},  # LiveKit Node: ms
        {"role": "customer", "text": "Hello", "at": (T0 + timedelta(seconds=1)).isoformat()},
        {"role": "weird", "text": "x", "offset_s": 3},
    ]
    once = normalize_turns(turns, T0)
    assert [(t["role"], t["offset_s"]) for t in once] == [("user", 1.0), ("agent", 2.0), ("system", 3.0)]
    assert normalize_turns(once, T0) == once


def test_agent_and_release_are_matched(client, token, files):
    client.put(f"{WS}/files", json={"files": files})
    client.post(f"{WS}/releases", json={})
    by_ref = ingest(client, token, session("a", agent="livekit:tonys-pizza", release=1)).json()
    by_platform_id = ingest(client, token, session("b", agent="tonys-pizza")).json()
    unknown = ingest(client, token, session("c", agent="nobody", release=7)).json()
    assert by_ref["warnings"] == [] and by_platform_id["warnings"] == []
    assert len(unknown["warnings"]) == 2
    a, b, c = (client.get(f"{WS}/sessions/{x['id']}").json() for x in (by_ref, by_platform_id, unknown))
    assert (a["agent_id"], a["agent_ref"], a["release_id"]) == ("tonys-pizza", "livekit:tonys-pizza", 1)
    assert b["agent_id"] == "tonys-pizza" and b["release_id"] is None
    assert (c["agent_id"], c["agent_ref"], c["release_id"]) == (None, "nobody", None)


def test_list_filters_and_cursor(client, token):
    for i in range(5):
        ingest(client, token, session(f"v{i}", started_at=(T0 + timedelta(minutes=i)).isoformat(), ended_at=None))
    ingest(client, token, session("t0", channel="text", agent="sakura-sushi", platform="api", started_at=(T0 + timedelta(hours=1)).isoformat()))
    ingest(client, token, session("e0", turns=[], started_at=(T0 - timedelta(days=1)).isoformat()))

    def ids(**params):
        return [s["external_id"] for s in client.get(f"{WS}/sessions", params=params).json()["items"]]

    assert ids() == ["t0", "v4", "v3", "v2", "v1", "v0", "e0"]
    assert ids(channel="text") == ["t0"]
    assert ids(agent="sakura-sushi") == ["t0"]
    assert ids(platform="api") == ["t0"]
    assert ids(status="partial") == ["e0"]
    assert ids(since=T0.isoformat(), until=(T0 + timedelta(minutes=3)).isoformat()) == ["v2", "v1", "v0"]
    assert client.get(f"{WS}/sessions", params={"status": "nope"}).status_code == 422

    page = client.get(f"{WS}/sessions", params={"limit": 3}).json()
    seen = [s["external_id"] for s in page["items"]]
    while page["next_cursor"]:
        page = client.get(f"{WS}/sessions", params={"limit": 3, "cursor": page["next_cursor"]}).json()
        seen += [s["external_id"] for s in page["items"]]
    assert seen == ids()
    assert client.get(f"{WS}/sessions", params={"cursor": "garbage"}).status_code == 400
    assert client.get(f"{WS}/sessions/nope").status_code == 404


def test_import_cli(client, tmp_path, capsys):
    store = client.app.state.store
    url = store.engine.url.render_as_string(hide_password=False)
    lines = [json.dumps(session(f"imp-{i}", platform="import")) for i in range(3)]
    path = tmp_path / "sessions.jsonl"
    path.write_text("\n".join(lines + ["", '{"platform": "import"}']) + "\n")

    with pytest.raises(SystemExit) as exit:
        cli(["import", str(path), "--workspace", "demo", "--database-url", url])
    assert exit.value.code == 1
    out, err = capsys.readouterr()
    assert "3 created, 0 updated, 0 unchanged, 1 failed (pipeline: inline)" in out
    assert "line 5: external_id: Field required" in err

    path.write_text("\n".join(lines) + "\n")
    cli(["import", str(path), "--workspace", "demo", "--database-url", url])  # again: nothing changes
    assert "0 created, 0 updated, 3 unchanged, 0 failed" in capsys.readouterr().out

    items = client.get(f"{WS}/sessions").json()["items"]
    assert sorted(s["external_id"] for s in items) == ["imp-0", "imp-1", "imp-2"]
    assert all(s["processed_at"] and s["turn_count"] == 3 for s in items)


def test_broker_delivers_per_workspace():
    broker, got, other = Broker(), [], []
    stop = broker.subscribe("demo", got.append)
    broker.subscribe("elsewhere", other.append)
    broker.subscribe("demo", lambda e: 1 / 0)  # a broken subscriber doesn't stop the rest
    broker.publish({"workspace": "demo", "id": "1"})
    stop()
    broker.publish({"workspace": "demo", "id": "2"})
    assert got == [{"workspace": "demo", "id": "1"}] and other == []


def test_ingest_and_pipeline_publish_session_events(client, token):
    """On SQLite the store publishes after commit; on Postgres it NOTIFYs and the app's listener republishes."""
    with TestClient(client.app) as live:  # runs the lifespan, which starts the Postgres listener
        listener = getattr(live.app.state, "pg_listener", None)
        if listener:
            assert listener.ready.wait(5)
        got = []
        live.app.state.store.sessions.broker.subscribe("demo", got.append)
        sid = ingest(live, token, session()).json()["id"]
        deadline = time.time() + 5
        while len(got) < 3 and time.time() < deadline:
            time.sleep(0.05)
    # created, then normalized, then the router's "no model configured"
    assert [(e["type"], e["id"]) for e in got] == [("created", sid), ("updated", sid), ("updated", sid)]
    assert got[1]["status"] == "received"


def test_stream_endpoint_sends_events(client, token):
    """The SSE endpoint, driven directly: it relays broker events for its workspace as `event: session`."""
    import asyncio

    app = client.app
    route = next(r for r in app.routes if getattr(r, "path", "") == "/v1/workspaces/{workspace}/sessions/stream")

    class FakeRequest:
        async def is_disconnected(self):
            return False

    async def run():
        res = await route.endpoint("demo", FakeRequest())
        it = res.body_iterator
        assert (await it.__anext__()).startswith("retry:")
        app.state.store.sessions.broker.publish({"type": "created", "workspace": "demo", "id": "s1", "status": "received"})
        msg = await asyncio.wait_for(it.__anext__(), 2)
        await it.aclose()
        return msg

    msg = asyncio.run(run())
    assert msg.startswith("event: session\ndata: ") and json.loads(msg.split("data: ", 1)[1])["id"] == "s1"


def test_pipeline_skips_stale_and_missing_sessions(client, token):
    from sopops.pipeline import SessionRef, process_session

    store = client.app.state.store
    sid = ingest(client, token, session()).json()["id"]
    assert process_session(store, SessionRef(workspace_id="demo", session_id="gone", revision="x")).skipped
    stale = process_session(store, SessionRef(workspace_id="demo", session_id=sid, revision="an-older-payload"))
    assert stale.skipped and stale.status == "received"
