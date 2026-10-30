"""Pausing a workspace's analysis: sessions still come in with their transcripts, nothing calls a model, and resuming
checks what was held."""

import test_checks
from conftest import WS
from test_checks import session

from sopops_server.ai.signatures import CheckRules
from sopops_server.analysis import PAUSED, skipped
from sopops_server.auth.access import ADMIN, SESSION, kind_of

env = test_checks.env  # the checks' workspace, with fake models


def model_calls(env):
    return [c for c in env.fake.calls if c[1].__name__ in ("RouteSession", "CheckRules")]


def test_paused_sessions_come_in_unchecked_and_resume_checks_them(env):
    assert env.client.get(f"{WS}/analysis").json() == {"paused": False, "paused_at": None, "paused_by": None, "skip_tags": [], "queued": 0}
    paused = env.client.put(f"{WS}/analysis", json={"paused": True}).json()
    assert paused["paused"] is True and paused["paused_at"]

    s = env.send(session("while-paused"))
    assert s["status"] == "received" and s["status_reason"] == PAUSED and s["checks"] == []
    assert len(s["turns"]) == 8  # the transcript is there
    assert model_calls(env) == [] and not any(c[1] is CheckRules for c in env.fake.calls)

    for path, body in ((f"{WS}/patterns/recluster", {}), (f"{WS}/calibrate", {})):
        r = env.client.post(path, json=body)
        assert r.status_code == 409 and PAUSED in r.text

    resumed = env.client.put(f"{WS}/analysis", json={"paused": False}).json()
    assert resumed["paused"] is False and resumed["queued"] == 1
    s = env.client.get(f"{WS}/sessions/{s['id']}").json()
    assert s["status"] == "checked" and s["result"] == "deviated"
    assert model_calls(env)


def test_pausing_twice_keeps_when_it_started_and_resuming_twice_queues_nothing(env):
    first = env.client.put(f"{WS}/analysis", json={"paused": True}).json()
    assert env.client.put(f"{WS}/analysis", json={"paused": True}).json()["paused_at"] == first["paused_at"]
    env.send(session("held"))
    assert env.client.put(f"{WS}/analysis", json={"paused": False}).json()["queued"] == 1
    assert env.client.put(f"{WS}/analysis", json={"paused": False}).json()["queued"] == 0


def test_a_session_checked_before_the_pause_isnt_queued_again(env):
    env.send(session("before"))
    env.client.put(f"{WS}/analysis", json={"paused": True})
    assert env.client.put(f"{WS}/analysis", json={"paused": False}).json()["queued"] == 0


def test_owners_and_admins_pause_and_anyone_sees_it():
    assert kind_of("set_analysis") == ADMIN and kind_of("get_analysis") == SESSION


def test_sessions_with_a_skipped_tag_come_in_unchecked_and_unskipping_checks_them(env):
    env.client.put(f"{WS}/agent-tags/tonys-pizza", json={"tags": ["legacy"]})
    state = env.client.put(f"{WS}/analysis", json={"skip_tags": ["Legacy"]}).json()
    assert state["skip_tags"] == ["legacy"] and state["paused"] is False and state["queued"] == 0

    s = env.send(session("legacy-call"))
    assert s["status"] == "excluded" and s["status_reason"] == skipped("legacy") and s["checks"] == []
    assert s["tags"] == ["legacy"] and len(s["turns"]) == 8
    assert model_calls(env) == []

    other = env.send(session("tagged-by-sender", metadata={"sopops_tags": "pilot"}))  # still has the agent's legacy tag
    assert other["status"] == "excluded"

    assert env.client.put(f"{WS}/analysis", json={"skip_tags": []}).json()["queued"] == 2
    s = env.client.get(f"{WS}/sessions/{s['id']}").json()
    assert s["status"] == "checked" and model_calls(env)
    assert env.client.get(f"{WS}/analysis").json()["skip_tags"] == []


def test_a_session_without_the_tag_is_checked(env):
    env.client.put(f"{WS}/analysis", json={"skip_tags": ["legacy"]})
    assert env.send(session("new-prompt"))["status"] == "checked"
    assert env.client.put(f"{WS}/analysis", json={"skip_tags": ["no spaces!"]}).status_code == 422
