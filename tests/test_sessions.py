"""Sessions: canonical ingest, the ingest token, listing, the import CLI, live events and the inline pipeline."""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from conftest import WS, downgrade, fresh_db_url
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session as DB

from sopops_server import Sop, Store, create_app
from sopops_server.cli import main as cli
from sopops_server.events import Broker
from sopops_server.pipeline.normalize import normalize_turns
from sopops_server.pipeline.process import NO_MODEL
from sopops_server.schemas import SessionIn

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

    from sopops_server.orm import Token

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



# A customer's A/B variant is a whole document: voice pipeline settings, feature flags, nested sections.
NESTED = {
    "llm": "anthropic/claude-haiku-4-5",
    "pipeline": {"vad": {"threshold": 0.55, "min_silence_ms": 400}, "endpointing": "semantic", "fallbacks": ["a", "b"]},
    "flags": {"warm_transfer": True, "barge_in": False, "max_retries": 3},
    "tools": [{"name": "lookup", "timeout_s": 2.5}, {"name": "pay"}],
}


def test_a_nested_config_round_trips_and_identifies_its_version(client, token):
    from sopops_server import configs

    first = ingest(client, token, session("nested-1", config=NESTED, metadata={"sopops_voice": "ava"}))
    assert first.status_code == 201 and first.json()["warnings"] == []
    s = client.get(f"{WS}/sessions/{first.json()['id']}").json()
    assert s["config"] == {**NESTED, "voice": "ava"}  # objects and arrays are kept whole, nested keys as sent
    assert s["config_hash"] == configs.config_hash(s["config"])

    # The same content, sent in another order, is the same version; a changed nested value is another one.
    same = ingest(client, token, session("nested-2", config={"tools": NESTED["tools"], "flags": dict(reversed(list(NESTED["flags"].items()))),
                                                             "pipeline": NESTED["pipeline"], "llm": NESTED["llm"], "voice": "ava"}))
    deeper = {**NESTED["pipeline"], "vad": {"threshold": 0.9, "min_silence_ms": 400}}
    other = ingest(client, token, session("nested-3", config={**NESTED, "pipeline": deeper, "voice": "ava"}))
    by_id = {x["external_id"]: x for x in client.get(f"{WS}/sessions").json()["items"]}
    assert by_id["nested-2"]["config_hash"] == s["config_hash"]
    assert by_id["nested-3"]["config_hash"] != s["config_hash"]
    assert same.status_code == 201 and other.status_code == 201

    # A close merges settings learned during the call: a nested setting is replaced whole.
    client.post(f"{WS}/sessions/open", json={"platform": "livekit", "external_id": "nested-4", "config": NESTED},
                headers={"Authorization": f"Bearer {token}"})
    client.post(f"{WS}/sessions/livekit/nested-4/close", json={"config": {"flags": {"warm_transfer": False}}},
                headers={"Authorization": f"Bearer {token}"})
    live = next(x for x in client.get(f"{WS}/sessions").json()["items"] if x["external_id"] == "nested-4")
    assert live["config"] == {**NESTED, "flags": {"warm_transfer": False}}


def test_a_nested_config_over_the_size_limit_is_left_out_with_a_warning(client, token):
    deep: object = "bottom"
    for _ in range(9):  # 9 levels, one past the limit
        deep = {"section": deep}
    big = {"notes": ["x" * 500] * 32}  # well over 8,192 characters of JSON
    r = ingest(client, token, session("nested-big", config={"pipeline": NESTED["pipeline"], "deep": deep, "big": big}))
    assert r.status_code == 201
    assert [w.split("'")[1] for w in r.json()["warnings"]] == ["deep", "big"]
    s = client.get(f"{WS}/sessions/{r.json()['id']}").json()
    assert s["config"] == {"pipeline": NESTED["pipeline"]} and s["config_hash"]  # the session is kept, the rest of its config too


def test_the_unique_key_holds_in_the_database(client):
    from sqlalchemy.exc import IntegrityError
    from sqlmodel import Session as Db

    from sopops_server.orm import SessionRecord

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
    assert client.get(f"{WS}/sessions/{empty}").json()["status"] == "incomplete"
    flagged = ingest(client, token, session("room-cut", partial=True)).json()
    assert flagged["status"] == "incomplete"
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
    assert ids(status="incomplete") == ["e0"]
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


def test_tags_are_copied_from_the_agent_and_kept(client, token, files):
    client.put(f"{WS}/files", json={"files": files})
    client.post(f"{WS}/releases", json={})
    assert client.put(f"{WS}/agent-tags/tonys-pizza", json={"tags": ["Legacy", "pilot"]}).json() == {"tags": ["legacy", "pilot"]}
    assert client.put(f"{WS}/agent-tags/tonys-pizza", json={"tags": ["no spaces!"]}).status_code == 422

    old = ingest(client, token, session("old")).json()
    # the agent moves to sopops' prompts: sessions from now on say so, and the earlier one keeps "legacy"
    client.put(f"{WS}/agent-tags/tonys-pizza", json={"tags": ["sopops", "pilot"]})
    new = ingest(client, token, session("new", tags=["Store 12", "bad tag!"], metadata={"sopops_tags": "night-shift"})).json()
    assert new["warnings"] == ["Tag 'bad tag!' isn't valid (letters, digits and - _ . : /, up to 64 characters); it was left out."]
    assert client.get(f"{WS}/sessions/{old['id']}").json()["tags"] == ["legacy", "pilot"]
    got = client.get(f"{WS}/sessions/{new['id']}").json()
    assert got["tags"] == ["night-shift", "pilot", "sopops", "store-12"]
    assert "tags" not in (got["config"] or {})  # sopops_tags is a tag, not a setting

    # a re-send adds what it passes; the copy taken when the session came in stays
    client.put(f"{WS}/agent-tags/tonys-pizza", json={"tags": []})
    ingest(client, token, session("old", tags=["reviewed"], ended_at=(T0 + timedelta(seconds=99)).isoformat()))
    assert client.get(f"{WS}/sessions/{old['id']}").json()["tags"] == ["legacy", "pilot", "reviewed"]

    def ids(*tag):
        return [x["external_id"] for x in client.get(f"{WS}/sessions", params={"tag": list(tag)}).json()["items"]]

    assert ids("legacy") == ["old"] and ids("sopops") == ["new"] and sorted(ids("pilot")) == ["new", "old"]
    assert ids("pilot", "sopops") == ["new"] and ids("nothing") == []
    listed = client.get(f"{WS}/agent-tags").json()
    assert listed == {"agents": {}, "used": ["legacy", "night-shift", "pilot", "reviewed", "sopops", "store-12"]}

    assert client.put(f"{WS}/sessions/{old['id']}/tags", json={"tags": ["sopops"]}).json() == {"tags": ["sopops"]}
    assert ids("legacy") == []
    assert client.get(f"{WS}/insights/overview", params={"tag": "sopops", "start": (T0 - timedelta(days=1)).isoformat()}).json()["filters"]["tags"] == ["sopops"]


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
    routes = [x for r in app.routes for x in getattr(getattr(r, "original_router", None), "routes", [r])]  # routers are included lazily
    route = next(r for r in routes if getattr(r, "path", "") == "/v1/workspaces/{workspace}/sessions/stream")

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
    from sopops_server.pipeline.process import process_session
    from sopops_server.pipeline.types import SessionRef

    store = client.app.state.store
    sid = ingest(client, token, session()).json()["id"]
    assert process_session(store, SessionRef(workspace_id="demo", session_id="gone", revision="x")).skipped
    stale = process_session(store, SessionRef(workspace_id="demo", session_id=sid, revision="an-older-payload"))
    assert stale.skipped and stale.status == "received"


# --- streaming (optional): open, events, close ---------------------------------------------------------------------


def stream_post(client, token, path, body=None):
    return client.post(f"{WS}/sessions{path}", json=body, headers={"Authorization": f"Bearer {token}"})


def turn(seq, role, text):
    return {"seq": seq, "type": "turn", "turn": {"role": role, "text": text, "offset_s": seq}}


def test_streamed_events_are_idempotent_ordered_and_checked_on_close(client, token):
    opened = stream_post(client, token, "/open", {"platform": "livekit", "external_id": "live-1", "agent": "tonys-pizza"})
    assert opened.status_code == 201 and opened.json()["status"] == "open"
    again = stream_post(client, token, "/open", {"platform": "livekit", "external_id": "live-1"})
    assert again.status_code == 200 and again.json()["id"] == opened.json()["id"]

    first = stream_post(client, token, "/livekit/live-1/events", {"events": [turn(2, "agent", "Pickup or delivery?"), turn(0, "agent", "Tony's Pizza.")]})
    assert first.json() == {"accepted": 2, "duplicates": 0}
    tool = {"seq": 3, "type": "tool_call", "tool_call": {"name": "check_delivery_zone", "arguments": {"zip": "10001"}}}
    second = stream_post(client, token, "/livekit/live-1/events", {"events": [turn(0, "agent", "Tony's Pizza."), turn(1, "user", "A large pepperoni."), tool]})
    assert second.json() == {"accepted": 2, "duplicates": 1}
    s = client.get(f"{WS}/sessions/{opened.json()['id']}").json()
    assert s["status"] == "open" and [t["text"] for t in s["turns"]] == ["Tony's Pizza.", "A large pepperoni.", "Pickup or delivery?"]
    assert [c["name"] for c in s["tool_calls"]] == ["check_delivery_zone"]

    closed = stream_post(client, token, "/livekit/live-1/close", {})
    assert closed.status_code == 200 and closed.json()["updated"]
    s = client.get(f"{WS}/sessions/{opened.json()['id']}").json()
    assert s["status"] == "received" and s["status_reason"] == NO_MODEL and s["turn_count"] == 3  # the pipeline ran (no model here)
    late = stream_post(client, token, "/livekit/live-1/events", {"events": [turn(4, "agent", "Bye.")]})
    assert late.json() == {"accepted": 0, "duplicates": 1}  # closed: ignored


def test_the_final_full_post_wins_over_what_streamed(client, token):
    stream_post(client, token, "/open", {"platform": "livekit", "external_id": "room-1"})
    stream_post(client, token, "/livekit/room-1/events", {"events": [turn(0, "agent", "Hello?")]})
    done = ingest(client, token, session())
    assert done.status_code == 200 and done.json()["updated"]
    s = client.get(f"{WS}/sessions/{done.json()['id']}").json()
    assert s["status"] != "open" and "Hello?" not in [t["text"] for t in s["turns"]]


def test_events_need_an_open_session_and_the_ingest_scope(client, token):
    assert client.post(f"{WS}/sessions/open", json={"platform": "livekit", "external_id": "x"}).status_code == 401
    assert stream_post(client, token, "/livekit/nope/events", {"events": [turn(0, "user", "hi")]}).status_code == 404


def test_idle_open_sessions_close_as_incomplete(client, token):
    store = client.app.state.store
    opened = stream_post(client, token, "/open", {"platform": "livekit", "external_id": "idle-1"}).json()
    stream_post(client, token, "/livekit/idle-1/events", {"events": [turn(0, "user", "Hello?")]})
    assert store.sessions.close_idle("demo") == []  # not idle yet
    [ref] = store.sessions.close_idle("demo", idle=timedelta(0))
    s = client.get(f"{WS}/sessions/{opened['id']}").json()
    assert ref.session_id == opened["id"] and s["status"] == "incomplete" and "No events" in s["status_reason"]


def test_the_migration_renames_partial_sessions_to_incomplete(tmp_path):
    url = fresh_db_url(tmp_path)
    store = Store(url)
    sid = store.sessions.ingest("demo", SessionIn(**session("room-old", partial=True))).id
    downgrade(store.engine, "0013")  # as a database from before 0014 has it
    with DB(store.engine) as db, db.begin():
        assert db.exec(text("SELECT status FROM sessions WHERE id = :id").bindparams(id=sid)).one()[0] == "partial"
    again = Store(url, Sop())
    with DB(again.engine) as db:
        assert db.exec(text("SELECT status FROM sessions WHERE id = :id").bindparams(id=sid)).one()[0] == "incomplete"


def test_a_session_read_before_it_is_normalized_shows_normalized_turns(tmp_path):
    store = Store(fresh_db_url(tmp_path))
    sid = store.sessions.ingest("demo", SessionIn(**session("room-raw"))).id  # stored as sent; the pipeline hasn't run
    got = TestClient(create_app(store)).get(f"{WS}/sessions/{sid}")
    assert got.status_code == 200, got.text
    assert [t["role"] for t in got.json()["turns"]] == ["agent", "user", "tool"]
