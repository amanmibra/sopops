"""A/B tests: each session's config (what it ran with) and Insights' version comparison. A version is the sessions
with the same agent, release and config hash; versions are compared each against the baseline with the release
comparison's minimum n, intervals and flags."""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from conftest import SOPC_BIN, WS, fresh_db_url
from fakes import pattern_answer, pattern_reply
from fastapi.testclient import TestClient

from sopops_server import Sopc, Store, configs, create_app
from sopops_server.ai.fakes import FakeModels, FakeSystemOne
from sopops_server.ai.signatures import CheckRules, field_name
from sopops_server.capture import agent_config
from sopops_server.jobs import Jobs

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
ALLERGEN = "allergen-check"
GPT = {"llm": "openai/gpt-4.1-mini", "tts": "cartesia/sonic-2"}
HAIKU = {"llm": "anthropic/claude-haiku-4-5", "tts": "cartesia/sonic-2"}


def test_configs_come_from_the_payload_and_sopops_metadata():
    test, config = configs.from_metadata({"sopops_test": "haiku-vs-gpt", "sopops_llm": "x/y", "sopops_release": 3, "sopops_agent": "a", "room": "r",
                                          "sopops": {"voice": "ava", "llm": "ignored"}})
    assert test == "haiku-vs-gpt" and config == {"llm": "x/y", "voice": "ava"}
    assert configs.merge({"llm": "a/b", "stt": "unknown", "x": None, "Temp": 0.2}, {"llm": "c/d"}) == {"llm": "c/d", "temp": 0.2}
    kept, warnings = configs.fit({"llm": "a" * 2000, **{f"k{i}": i for i in range(40)}})
    assert "llm" not in kept and len(kept) == 32 and len(warnings) == 2
    stored, h, prompt = configs.stored({"llm": "a/b", "prompt": "You are a host."})
    assert prompt == "You are a host." and stored["prompt"] == configs.prompt_hash(prompt) and h == configs.config_hash(stored)
    assert configs.config_hash({}) is None and configs.config_hash({"a": 1, "b": 2}) == configs.config_hash({"b": 2, "a": 1})
    assert (configs.model_name("openai", "gpt-4.1"), configs.model_name("livekit", "openai/gpt-4.1"), configs.model_name("x", "unknown")) == \
        ("openai/gpt-4.1", "openai/gpt-4.1", None)



def test_a_nested_config_is_kept_whole_and_hashes_by_its_content():
    """A setting can be a whole object or array: it survives cleaning, and two sessions that ran the same one are
    one version, however the payload was ordered."""
    pipeline = {"vad": {"threshold": 0.55, "min_silence_ms": 400}, "endpointing": "semantic", "fallbacks": ["a", "b"]}
    config = configs.clean({"LLM": " openai/gpt-4.1-mini ", " Pipeline ": pipeline, "flags": {"warm_transfer": True, "barge_in": False},
                            "tools": [{"name": "lookup", "timeout_s": 2.5}], "empty": {}, "blank": [], "gone": "unknown", "nothing": None})
    assert config == {"llm": "openai/gpt-4.1-mini", "pipeline": pipeline, "flags": {"warm_transfer": True, "barge_in": False},
                      "tools": [{"name": "lookup", "timeout_s": 2.5}]}  # nested keys stay as sent; blanks and empties don't

    reordered = configs.clean({"tools": [{"timeout_s": 2.5, "name": "lookup"}], "flags": {"barge_in": False, "warm_transfer": True},
                               "pipeline": {"fallbacks": ["a", "b"], "endpointing": "semantic", "vad": {"min_silence_ms": 400, "threshold": 0.55}},
                               "llm": "openai/gpt-4.1-mini"})
    assert configs.config_hash(reordered) == configs.config_hash(config)
    deeper = configs.clean({**config, "pipeline": {**pipeline, "vad": {"threshold": 0.6, "min_silence_ms": 400}}})
    assert configs.config_hash(deeper) != configs.config_hash(config)
    assert configs.config_hash(configs.clean({**config, "tools": [{"name": "lookup", "timeout_s": 2.5}, {"name": "pay"}]})) != configs.config_hash(config)
    # later configs replace a nested setting whole, never merge into it
    assert configs.merge(config, {"pipeline": {"endpointing": "fixed"}})["pipeline"] == {"endpointing": "fixed"}


def test_a_nested_config_over_a_limit_is_left_out_with_a_warning():
    deep: object = "bottom"
    for _ in range(configs.MAX_DEPTH + 1):
        deep = {"section": deep}
    kept, warnings = configs.fit(configs.clean({"ok": {"vad": {"threshold": 0.5}}, "deep": deep,
                                                "big": {"notes": ["x" * 400] * 32}, "prompt": "p" * (configs.MAX_PROMPT - 1)}))
    assert list(kept) == ["ok", "prompt"]
    assert [w.split("'")[1] for w in warnings] == ["deep", "big"]
    assert f"{configs.MAX_DEPTH} levels" in warnings[0] and "8,192 characters of JSON" in warnings[1]
    assert configs.fit(configs.clean({"ok": {"a": [1, 2, 3]}}))[1] == []


def test_platform_agents_settings():
    el = {"conversation_config": {"agent": {"prompt": {"llm": "gpt-4o-mini", "temperature": 0}}, "tts": {"model_id": "eleven_flash_v2_5", "voice_id": "v1"}}}
    assert agent_config.elevenlabs(el, {"tts": {"voice_id": "v2"}}) == {"llm": "gpt-4o-mini", "temperature": 0, "tts": "elevenlabs/eleven_flash_v2_5", "voice": "v2"}
    assert agent_config.elevenlabs(None) == {}
    assert agent_config.vapi({"model": {"provider": "anthropic", "model": "claude-haiku-4-5"}, "voice": {"provider": "vapi", "voiceId": "Elliot"}}) == \
        {"llm": "anthropic/claude-haiku-4-5", "tts": "vapi", "voice": "Elliot"}


def model(role, signature, inputs):
    """DEV in the user's turn: never rule 1 violated. WARN: warning sign 1 present."""
    if signature is not CheckRules:
        return pattern_reply(role, signature, inputs)
    text = inputs["transcript"]
    rules = []
    for key in inputs["rules_to_check"]:
        status, ev = {"step": "done", "never": "ok", "warning": "absent"}[key.split("_")[0]], []
        if key == "never_1" and "DEV" in text:
            status, ev = "violated", [{"turn": 2, "quote": "totally safe"}]
        if key == "warning_1" and "WARN" in text:
            status, ev = "present", [{"turn": 1, "quote": "EpiPen"}]
        rules.append({"rule": key, "reason": "fake", "status": status, "evidence": ev})
    return {"rules": rules, "goal_reason": "g", "goal_reached": "yes"}


def decide(state, questions):
    if "findings" in state["inputs"]:
        return pattern_answer(state, questions)
    mine = field_name(f"p_{ALLERGEN}")
    return {k: 0.95 if k.startswith(mine) else 0.03 for k in questions}


@pytest.fixture(scope="module")
def env(tmp_path_factory, request):
    from conftest import FIXTURE, read_files

    if not SOPC_BIN:
        pytest.skip("needs sopc")
    tmp = tmp_path_factory.mktemp("versions")
    store = Store(fresh_db_url(tmp), Sopc(SOPC_BIN))
    os.environ["SOPOPS_CHECK_MODE"] = "review"
    request.addfinalizer(lambda: os.environ.pop("SOPOPS_CHECK_MODE", None))
    client = TestClient(create_app(store, Jobs(store, use_hatchet=False, ai=FakeModels(model, FakeSystemOne(decide)))))
    client.put(f"{WS}/files", json={"files": read_files(FIXTURE / "sops")})
    client.post(f"{WS}/releases", json={})
    token = client.post(f"{WS}/tokens").json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    n = {"i": 0}

    def send(at, flags="", **extra):
        n["i"] += 1
        body = {"platform": "livekit", "external_id": f"v{n['i']}", "agent": "tonys-pizza", "release": 1, "started_at": at.isoformat(),
                "turns": [{"role": "agent", "text": "Hi"}, {"role": "user", "text": f"{flags} peanut allergy, EpiPen"}, {"role": "agent", "text": "It is totally safe."}],
                **extra}
        r = client.post(f"{WS}/sessions", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    # A (gpt, from `config` and `test`): 12 sessions, 8 break the never rule, 3 with the warning sign.
    # B (haiku, as LiveKit attributes in metadata): 12 sessions, none break it. Then 2 sessions with no config.
    for i in range(12):
        send(T0 + timedelta(minutes=i), "DEV" if i < 8 else "WARN" if i < 11 else "", test="haiku-vs-gpt", config=GPT)
    for i in range(12):
        send(T0 + timedelta(hours=1, minutes=i), metadata={"sopops_test": "haiku-vs-gpt", **{f"sopops_{k}": v for k, v in HAIKU.items()}})
    for i in range(2):
        send(T0 + timedelta(hours=2, minutes=i))
    params = {"start": T0.isoformat(), "end": (T0 + timedelta(days=1)).isoformat()}
    return SimpleNamespace(store=store, client=client, params=params, send=send)


def get(env, **extra):
    r = env.client.get(f"{WS}/insights/versions", params={**env.params, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_sessions_keep_their_config(env):
    items = env.client.get(f"{WS}/sessions", params={"limit": 100}).json()["items"]
    tagged = [s for s in items if s["test"]]
    assert len(tagged) == 24 and {s["config_hash"] for s in tagged} == {configs.config_hash(GPT), configs.config_hash(HAIKU)}
    assert sum(1 for s in items if s["config"] is None) == 2


def test_versions_are_labeled_and_compared_against_the_baseline(env):
    v = get(env, test="haiku-vs-gpt")
    assert [x["label"] for x in v["versions"]] == ["A", "B"] and [x["config"]["llm"] for x in v["versions"]] == [GPT["llm"], HAIKU["llm"]]
    assert v["selected"] == [x["id"] for x in v["versions"]] and v["baseline"] == v["versions"][0]["id"]
    assert v["differs"] == ["llm"] and v["tests"] == [{"name": "haiku-vs-gpt", "sessions": 24, "versions": 2}] and v["untagged"] == 0
    [proc] = v["procedures"]
    a, b = proc["cells"]
    assert (a["n"], a["pass_rate"], a["flag"], a["triggered"], a["trigger_rate"]) == (12, 33, None, 12, 100)
    assert (b["n"], b["pass_rate"], b["delta_points"], b["flag"]) == (12, 100, 67, "improved")
    never = next(r for r in v["rules"] if r["kind"] == "never" and r["index"] == 1)
    assert [(c["n"], c["pass_rate"], c["flag"]) for c in never["cells"]] == [(12, 33, None), (12, 100, "improved")]
    warning = next(r for r in v["rules"] if r["kind"] == "warning" and r["index"] == 1)
    assert [(c["present"], c["present_rate"], c["flag"]) for c in warning["cells"]] == [(3, 25, None), (0, 0, None)]


def test_without_a_test_every_version_in_the_window_counts(env):
    v = get(env)
    assert [x["label"] for x in v["versions"]] == ["A", "B", "C"] and v["versions"][2]["config_hash"] is None
    assert v["untagged"] == 2 and v["differs"] == ["llm", "tts"]  # C reported nothing, so tts differs too
    flipped = get(env, test="haiku-vs-gpt", v=[v["versions"][1]["id"], v["versions"][0]["id"]])
    assert flipped["baseline"] == v["versions"][1]["id"] and flipped["procedures"][0]["cells"][1]["flag"] == "regressed"
    assert get(env, test="nope")["versions"] == []


def test_a_prompt_is_stored_once_and_a_close_adds_settings(env):
    out = env.send(T0 + timedelta(hours=3), test="prompts", config={"prompt": "You are Tony's host.", "llm": "a/b"})
    again = env.send(T0 + timedelta(hours=3, minutes=1), test="prompts", config={"prompt": "You are Tony's host.", "llm": "a/b"})
    v = get(env, test="prompts")
    [only] = v["versions"]
    assert only["sessions"] == 2 and only["prompt"] == "You are Tony's host." and len(only["config"]["prompt"]) == 16
    assert out["warnings"] == [] and again["id"] != out["id"]

    c = env.client
    c.post(f"{WS}/sessions/open", json={"platform": "livekit", "external_id": "live-1", "config": {"llm": "a/b", "prompt": "P"}})
    c.post(f"{WS}/sessions/livekit/live-1/close", json={"test": "late", "config": {"tts": "c/d"}})
    s = next(x for x in c.get(f"{WS}/sessions", params={"limit": 100}).json()["items"] if x["external_id"] == "live-1")
    assert s["test"] == "late" and s["config"] == {"llm": "a/b", "prompt": configs.prompt_hash("P"), "tts": "c/d"}
    assert s["config_hash"] == configs.config_hash(s["config"])
