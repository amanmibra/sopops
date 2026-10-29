"""Model spend: every model call recorded with its tokens and estimated cost, for its workspace, session and purpose
(through DSPy's usage tracker, on the real client path and the fakes alike), and GET …/usage over a window. And
GET …/usage/calls: the calls a workspace sent in a period."""

from datetime import datetime, timedelta, timezone

import dspy
import pytest
from conftest import SOP_BIN, WS
from sqlmodel import Session, select
from test_review import ALLERGEN, env, rule_pattern, verdict  # noqa: F401  (env is a fixture)
from test_sessions import ingest, session, stream_post, token  # noqa: F401  (token is a fixture)

from sopops_server import Sop, Store
from sopops_server.ai import spend
from sopops_server.ai.fakes import FakeEngine
from sopops_server.ai.lm import Models
from sopops_server.ai.signatures import SplitRule
from sopops_server.orm import ModelCall

PRICED = '{"fake/reasoning": [3, 15]}'  # the fake decisions (fake-jev) stay unpriced


def calls(store, **where):
    with Session(store.engine) as db:
        return [c for c in db.exec(select(ModelCall)) if all(getattr(c, k) == v for k, v in where.items())]


def test_prices_match_by_name_prefix_and_env(monkeypatch):
    assert spend.price_of("anthropic/claude-haiku-4-5") == (1.0, 5.0)
    assert spend.price_of("claude-haiku-4-5-20251001") == (1.0, 5.0)  # a dated snapshot
    assert spend.price_of("jev-1.13.0") == (0.042, 0.0) and spend.cost("jev-1.13.0", 1_000_000, 500) == pytest.approx(0.042)
    assert spend.price_of("openai/some-model") is None and spend.cost("openai/some-model", 10, 10) is None
    monkeypatch.setenv(spend.PRICES_ENV, '{"openai/some-model": {"input": 2, "output": 8}, "jev": [0.05, 0]}')
    assert spend.price_of("openai/some-model") == (2.0, 8.0) and spend.price_of("jev-1.13.0") == (0.05, 0.0)
    assert spend.prices()["jev"][2] == "env"


def test_the_real_client_path_is_recorded_through_dspy(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'u.db'}", Sop(SOP_BIN))
    lm = dspy.LM("anthropic/claude-haiku-4-5", engine=FakeEngine(lambda outs, text: {"checks": {"condition": None, "actions": ["a"], "forbidden": []}}), cache=False)
    ai = Models({"fast": "anthropic/claude-haiku-4-5", "reasoning": "anthropic/claude-haiku-4-5"}, jev=False)
    ai._clients["anthropic/claude-haiku-4-5"] = lm
    ai.predict("reasoning", SplitRule, kind="step", rule="Ask for the name", procedure="p")  # outside any scope: not recorded
    with spend.scope(store.engine, "demo", "check", "s-1"):
        ai.predict("reasoning", SplitRule, kind="step", rule="Ask for the name", procedure="p")
        with spend.purpose("review"):
            ai.predict("reasoning", SplitRule, kind="step", rule="Ask for the phone number", procedure="p")
    got = calls(store)
    assert [(c.workspace_id, c.session_id, c.purpose, c.model) for c in got] == [("demo", "s-1", "check", "anthropic/claude-haiku-4-5"),
                                                                               ("demo", "s-1", "review", "anthropic/claude-haiku-4-5")]
    c = got[0]
    assert c.input_tokens > 0 and c.output_tokens > 0 and c.cost_usd == pytest.approx((c.input_tokens * 1 + c.output_tokens * 5) / 1e6)


def test_usage_by_purpose_model_session_and_day(env, monkeypatch):
    monkeypatch.setenv(spend.PRICES_ENV, PRICED)
    sids = [env.send("A") for _ in range(5)]
    verdict(env, rule_pattern(env)["pattern_id"], "real")
    assert env.client.post(f"{WS}/calibrate", json={"procedure": ALLERGEN, "dry_run": True}).status_code == 200

    route = calls(env.store, purpose="route")
    assert {c.session_id for c in route} == set(sids) and {c.model for c in route} == {"fake-jev"}
    assert {c.session_id for c in calls(env.store, purpose="review")} == set(sids)  # review mode: the reviewer checks every rule
    assert calls(env.store, purpose="calibrate") and all(c.session_id is None for c in calls(env.store, purpose="calibrate"))  # from the fit's threads too
    assert calls(env.store, purpose="theme")
    assert all(c.input_tokens > 0 for c in calls(env.store))

    u = env.client.get(f"{WS}/usage", params={"window": "7d"}).json()
    every = calls(env.store)
    assert u["total"]["calls"] == len(every) and u["total"]["cost_usd"] == pytest.approx(sum(c.cost_usd or 0 for c in every), abs=1e-6)
    assert u["total"]["unpriced_calls"] == sum(c.cost_usd is None for c in every) > 0  # fake-jev has no price
    purposes = {p["purpose"]: p for p in u["by_purpose"]}
    assert {"route", "review", "theme", "calibrate"} <= set(purposes) and purposes["review"]["cost_usd"] > 0
    assert {m["model"] for m in u["by_model"]} >= {"fake-jev", "fake/reasoning"}
    assert u["sessions"] == 5 and u["per_session_usd"] == pytest.approx(sum(c.cost_usd or 0 for c in every if c.session_id) / 5, abs=1e-6)
    assert len(u["daily"]) in (7, 8) and sum(d["calls"] for d in u["daily"]) == len(every)

    s = env.client.get(f"{WS}/sessions/{sids[0]}").json()
    mine = calls(env.store, session_id=sids[0])
    assert s["model_calls"] == len(mine) and s["cost_usd"] == pytest.approx(sum(c.cost_usd or 0 for c in mine), abs=1e-6)
    month_ago = datetime.now(timezone.utc) - timedelta(days=30)
    before = env.client.get(f"{WS}/usage", params={"start": (month_ago - timedelta(days=7)).isoformat(), "end": month_ago.isoformat()}).json()
    assert before["total"]["calls"] == 0 and before["per_session_usd"] is None

    p = env.client.get("/v1/model-prices").json()
    assert {"model": "fake/reasoning", "input_per_m": 3.0, "output_per_m": 15.0, "source": "env"} in p["prices"]
    assert {r["role"]: r["input_per_m"] for r in p["roles"]} == {"fast": None, "reasoning": None, "decide": None}  # no models configured here


def test_calls_in_a_period_count_sessions_received_in_full_by_start(client, token):
    month = datetime(2025, 9, 1, tzinfo=timezone.utc)
    starts = [month - timedelta(seconds=1), month, month + timedelta(days=2, hours=23), month + timedelta(days=2, hours=23, minutes=5), datetime(2025, 10, 1, tzinfo=timezone.utc)]
    for i, at in enumerate(starts):
        assert ingest(client, token, session(f"room-{i}", started_at=at.isoformat())).status_code == 201
    assert stream_post(client, token, "/open", {"platform": "livekit", "external_id": "live-1"}).status_code == 201

    got = client.get(f"{WS}/usage/calls", params={"start": month.isoformat(), "end": "2025-10-01T00:00:00Z"}).json()
    assert got["calls"] == 3  # [start, end): the session a second before and the one at `end` are out
    assert len(got["daily"]) == 30 and got["daily"][0] == {"day": "2025-09-01", "calls": 1} and got["daily"][2] == {"day": "2025-09-03", "calls": 2}
    assert sum(d["calls"] for d in got["daily"]) == 3
    assert client.get(f"{WS}/usage/calls", params={"start": "2025-09-15T00:00:00Z"}).json()["end"].startswith("2025-10-01T00:00:00")  # to the month's end

    # By default, this UTC month: the open session started in it, but isn't a call until it's closed.
    now = client.get(f"{WS}/usage/calls").json()
    assert now["start"].startswith(datetime.now(timezone.utc).strftime("%Y-%m-01T00:00:00")) and now["calls"] == 0
    assert stream_post(client, token, "/livekit/live-1/close").status_code == 200
    assert client.get(f"{WS}/usage/calls").json()["calls"] == 1

    assert client.get(f"{WS}/usage/calls", params={"start": month.isoformat(), "end": month.isoformat()}).status_code == 422
    assert client.get(f"{WS}/usage/calls", params={"start": "2025-01-01T00:00:00Z", "end": "2026-03-01T00:00:00Z"}).status_code == 422
