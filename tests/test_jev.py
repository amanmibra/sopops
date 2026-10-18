"""The Jev-first path with a fake Jev and fake DSPy models: routing, rule statuses from bands, escalation of flagged
and unsure rules to the reasoning model, splits stored once per rule version, segmenting, pattern assignment, config."""

from types import SimpleNamespace

import dspy
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from conftest import WS, fresh_db_url, SOPC_BIN
from sopops import Sopc, Store, create_app
from sopops.ai import classify, jev as jev_mod, lm
from sopops.ai.classify import Band, JevConfig, Line, segments
from sopops.ai.jev import Choice, FakeJev, Noul
from sopops.ai.signatures import CheckRules, SplitRule
from sopops.jobs import Jobs
from sopops.llm import FakeLLM
from sopops.orm import RuleChecks
from test_checks import ALLERGEN, DELIVERY, Scenario, checks_by_id, rules_by_key, session

NEVER_SAFE = 'Never say an item is "allergen-free" or "safe"'


class JevScenario:
    """Answers by what a question is about: `situation` per procedure name, and per rule text fragment a probability
    for its action (default: steps done, never rules not broken, warning signs' situations absent)."""

    def __init__(self, situations, actions=None, goal=0.9):
        self.situations, self.actions, self.goal = situations, actions or {}, goal

    def __call__(self, state, questions):
        out = {}
        for k, q in questions.items():
            if isinstance(q, Choice):
                out[k] = next(iter(q.options))
                continue
            text = q.instructions["question"]
            if k == "goal":
                out[k] = self.goal
            elif k.endswith("_situation") or k.endswith("_responded"):
                name = state["procedures"][k.split("_")[0]]["name"]
                out[k] = self.situations.get(name, 0.05)
            elif "situation" in q.instructions:
                out[k] = next((p for frag, p in self.actions.items() if frag in q.instructions["situation"]), 0.05)
            else:
                action = q.instructions["action"]
                default = 0.02 if action.startswith("Never") else 0.95
                out[k] = next((p for frag, p in self.actions.items() if frag in action), default)
                assert "transcript.utterances" in text
        return out


def split(role, signature, inputs):
    """A literal split: the rule's own text as its one action (a warning sign's as its situation)."""
    assert signature is SplitRule and role == "reasoning"
    if inputs["kind"] == "warning":
        return {"checks": {"condition": inputs["rule"], "actions": [], "forbidden": []}}
    return {"checks": {"condition": None, "actions": [inputs["rule"]], "forbidden": []}}


class Models:
    """Fake DSPy models: splits, and the reasoning model's review of escalated rules (`reviews`: key -> status)."""

    def __init__(self, reviews=None):
        self.reviews = reviews or {}

    def __call__(self, role, signature, inputs):
        if signature is SplitRule:
            return split(role, signature, inputs)
        assert signature is CheckRules
        rules = []
        for key in inputs["rules_to_check"]:
            status, ev = self.reviews.get(key, ("ok", []))
            rules.append({"rule": key, "reason": f"reviewed {key}", "status": status, "evidence": [{"turn": t, "quote": q} for t, q in ev]})
        return {"rules": rules, "goal_reason": "reviewed", "goal_reached": "yes"}


@pytest.fixture
def env(tmp_path, files, monkeypatch):
    store = Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN))
    llm = FakeLLM(Scenario({ALLERGEN: "applied"}, {}))
    models = lm.FakeModels(Models())
    lm.set_ai(models)
    jev = FakeJev(JevScenario({"Allergen check": 0.95, "Delivery": 0.9}))
    jev_mod.set_jev(jev)
    client = TestClient(create_app(store, Jobs(store, use_hatchet=False, llm=llm)))
    client.put(f"{WS}/files", json={"files": files})
    client.post(f"{WS}/releases", json={})
    token = client.post(f"{WS}/tokens").json()["token"]

    def send(body):
        r = client.post(f"{WS}/sessions", json=body, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code in (200, 201), r.text
        return client.get(f"{WS}/sessions/{r.json()['id']}").json()

    return SimpleNamespace(store=store, llm=llm, models=models, jev=jev, send=send, monkeypatch=monkeypatch)


def calls(models, signature):
    return [c for c in models.calls if c[1] is signature]


def test_jev_routes_and_clears_rules_without_the_llm(env):
    s = env.send(session())
    checks = checks_by_id(s)
    assert checks[ALLERGEN]["applicable"] and checks[DELIVERY]["applicable"]
    assert not any(c[0] == "fast" for c in env.llm.calls)  # routed by Jev, confidently
    a = checks[ALLERGEN]
    r = rules_by_key(a)
    assert [r[k]["status"] for k in ("step_1", "step_2", "never_1", "never_2", "warning_1")] == ["done", "done", "ok", "ok", "absent"]
    assert r["step_1"]["evidence"][0]["source"] == "transcript" and isinstance(r["step_1"]["evidence"][0]["turn"], int)
    assert a["model"] == "fake-jev" and a["prompt_version"] == classify.PROMPT_VERSION
    assert calls(env.models, CheckRules) == []  # nothing flagged, nothing escalated
    # every rule split once, with the agent's variables filled into the questions
    assert len(calls(env.models, SplitRule)) == 9  # allergen (6 rules) and delivery (3)
    asked = [q.instructions.get("action", "") for _, qs in env.jev.requests for q in qs.values() if isinstance(q, Noul)]
    assert any("tonys.com/allergens" in a for a in asked) and not any("{{menu_allergen_link}}" in a for a in asked)


def test_splits_are_stored_once_per_rule_version(env):
    env.send(session("one"))
    n = len(calls(env.models, SplitRule))
    env.send(session("two"))
    assert len(calls(env.models, SplitRule)) == n
    with Session(env.store.engine) as db:
        assert len(db.exec(select(RuleChecks)).all()) == n


def test_flagged_and_unsure_rules_go_to_the_reasoning_model(env):
    env.jev.responder = JevScenario({"Allergen check": 0.95}, {NEVER_SAFE: 0.9, "Name the specific allergen": 0.5})
    env.models.responder = Models({"never_1": ("violated", [(7, "totally safe for him")]), "step_2": ("done", [(3, "Got it, a peanut allergy.")])})
    s = env.send(session())
    a = checks_by_id(s)[ALLERGEN]
    r = rules_by_key(a)
    (_, _, inputs), = calls(env.models, CheckRules)
    assert inputs["rules_to_check"] == ["step_2", "never_1"]  # the unsure step and the flagged never rule only
    assert r["never_1"]["status"] == "violated" and r["never_1"]["reason"] == "reviewed never_1" and r["never_1"]["evidence"][0]["turn"] == 7
    assert r["step_2"]["status"] == "done" and r["step_1"]["reason"].startswith("Jev")
    assert a["result"] == "deviated" and a["model"] == "fake-jev+fake/reasoning"


def test_without_escalation_jev_decides_every_rule(env):
    env.monkeypatch.setenv("SOPOPS_JEV_ESCALATE", "off")
    env.jev.responder = JevScenario({"Allergen check": 0.95}, {NEVER_SAFE: 0.9, "Name the specific allergen": 0.4})
    s = env.send(session())
    r = rules_by_key(checks_by_id(s)[ALLERGEN])
    assert calls(env.models, CheckRules) == []
    assert r["never_1"]["status"] == "violated" and r["never_1"]["evidence"][0]["turn"] in (0, 2, 3, 4, 5, 7)  # an agent turn
    assert r["step_2"]["status"] == "skipped"  # before the last done step, so not "not reached"


def test_steps_after_the_last_done_step_were_not_reached(env):
    env.monkeypatch.setenv("SOPOPS_JEV_ESCALATE", "off")
    env.jev.responder = JevScenario({"Allergen check": 0.95, "Delivery": 0.9}, {"estimated delivery time": 0.05})
    s = env.send(session())
    assert rules_by_key(checks_by_id(s)[DELIVERY])["step_3"]["status"] == "not_reached"


def test_unsure_routing_falls_back_to_the_llm(env):
    env.jev.responder = JevScenario({"Allergen check": 0.5})
    s = env.send(session())
    assert [c[0] for c in env.llm.calls][:1] == ["fast"]
    assert checks_by_id(s)[ALLERGEN]["applicable"] and checks_by_id(s)[ALLERGEN]["prompt_version"]


def test_a_conversation_where_the_user_never_spoke_is_excluded(env):
    s = env.send(session("silent", turns=[("agent", "Tony's Pizza, how can I help?")]))
    assert s["status"] == "excluded"


def test_long_transcripts_are_cut_into_overlapping_segments():
    lines = [Line(i, f"U{i:03d}", "agent" if i % 2 else "user", "word " * 40) for i in range(600)]
    segs = segments({"procedure": {"name": "x"}}, lines, budget=28_000)
    assert len(segs) > 1 and all(len(s) <= 255 for s in segs)
    assert [x.turn for x in segs[1][:4]] == [x.turn for x in segs[0][-4:]]  # overlap
    assert {x.turn for s in segs for x in s} == set(range(600))


def test_patterns_are_assigned_with_a_choice():
    jev = FakeJev(lambda state, qs: {k: {"m1": 0.7, "m2": 0.2, "other": 0.1} if k == "f1" else {"m1": 0.3, "m2": 0.35, "other": 0.35} for k in qs})
    group = classify.AssignGroup(rule="Step 1: ask", modes={"m1": "A", "m2": "B"}, findings={"f1": {"reason": "x"}, "f2": {"reason": "y"}})
    assert classify.assign(jev, [group], JevConfig()) == {"f1": "m1", "f2": "m2"}
    assert classify.assign(jev, [group], JevConfig(assign_min=0.5)) == {"f1": "m1", "f2": "other"}


def test_config_from_env():
    cfg = JevConfig.from_env({"SOPOPS_JEV_CONFIG": '{"never": [0.1, 0.8, 0.6], "escalate_statuses": ["violated"]}', "SOPOPS_JEV_ESCALATE": "off"})
    assert cfg.never == Band(0.1, 0.8, 0.6) and cfg.escalate_statuses == ("violated",) and cfg.escalate is False
    with pytest.raises(ValueError):
        JevConfig.parse({"nope": 1})


@pytest.mark.parametrize("env_vars,on", [({}, False), ({"TYPESAFE_API_KEY": "k"}, True), ({"TYPESAFE_API_KEY": "k", "SOPOPS_JEV": "off"}, False), ({"SOPOPS_JEV": "on"}, True)])
def test_jev_is_on_with_a_key_unless_turned_off(env_vars, on):
    assert jev_mod.enabled(env_vars) is on


def test_models_run_a_dspy_signature_and_count_usage(monkeypatch):
    dummy = dspy.utils.DummyLM([{"checks": '{"condition": null, "actions": ["The agent asks."], "forbidden": []}'}])
    models = lm.Models({"fast": "x/fast", "reasoning": "x/reasoning"})
    monkeypatch.setattr(models, "lm", lambda role: dummy)
    pred = models.predict("reasoning", SplitRule, kind="step", rule="Ask", procedure="P")
    assert pred.checks.actions == ["The agent asks."] and models.usage.calls == 1
