"""The decision-first check with fake models: routing by decisions, rule statuses from thresholds and bands, evidence
(a Choice over utterance ids on a System One backend, LocateEvidence on a generative one), the reasoning model's review
of flagged and unsure rules, splits stored once per rule version, calibrated thresholds, segmenting, assignment,
settings, and decision signatures decoded from a generative model."""

from types import SimpleNamespace

import dspy
import pytest
from conftest import SOPC_BIN, WS, fresh_db_url
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from test_checks import ALLERGEN, DELIVERY, checks_by_id, rules_by_key, session

from sopops import Sopc, Store, create_app
from sopops.ai import calibration, decide
from sopops.ai.decide import Band, DecisionConfig, Line, segments
from sopops.ai.fakes import FakeEngine, FakeModels, FakeSystemOne
from sopops.ai.lm import Models, jev_enabled
from sopops.ai.signatures import CheckRules, LocateEvidence, SplitRule, field_name
from sopops.jobs import Jobs
from sopops.orm import RuleChecks

NEVER_SAFE = 'Never say an item is "allergen-free" or "safe"'


class Decisions:
    """Answers by what a question is about: `situations` per procedure key, and per rule text fragment a probability
    for its action (default: steps done, never rules not broken, warning signs' situations absent)."""

    def __init__(self, situations, actions=None, goal=0.9):
        self.situations, self.actions, self.goal = situations, actions or {}, goal

    def __call__(self, state, questions):
        out = {}
        for k, q in questions.items():
            if q["type"] == "choice":
                out[k] = next(iter(q["criteria"]))  # evidence: the first utterance offered
            elif k == "goal":
                out[k] = self.goal
            elif k.endswith("_situation") or k.endswith("_responded"):
                out[k] = next((p for key, p in self.situations.items() if k.startswith(field_name(f"p_{key}"))), 0.03)
            elif "situation" in q["instructions"]:
                out[k] = next((p for frag, p in self.actions.items() if frag in q["instructions"]["situation"]), 0.05)
            else:
                action = q["instructions"]["action"]
                default = 0.02 if action.startswith("Never") else 0.95
                out[k] = next((p for frag, p in self.actions.items() if frag in action), default)
        return out


class Generative:
    """The reasoning model: literal splits, and a review that gives `reviews` (key -> (status, evidence)), ok otherwise."""

    def __init__(self, reviews=None):
        self.reviews = reviews or {}

    def __call__(self, role, signature, inputs):
        if signature is SplitRule:
            if inputs["kind"] == "warning":
                return {"checks": {"condition": inputs["rule"], "actions": [], "forbidden": []}}
            return {"checks": {"condition": None, "actions": [inputs["rule"]], "forbidden": []}}
        if signature is LocateEvidence:
            return {"located": [{"key": k, "turn": 2, "quote": "Does anyone else"} for k in inputs["actions"]]}
        assert signature is CheckRules
        rules = []
        for key in inputs["rules_to_check"]:
            status, ev = self.reviews.get(key, ("ok", []))
            rules.append({"rule": key, "reason": f"reviewed {key}", "status": status, "evidence": [{"turn": t, "quote": q} for t, q in ev]})
        return {"rules": rules, "goal_reason": "reviewed", "goal_reached": "yes"}


@pytest.fixture
def env(tmp_path, files, monkeypatch):
    store = Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN))
    decider = FakeSystemOne(Decisions({ALLERGEN: 0.95, DELIVERY: 0.9}))
    fake = FakeModels(Generative(), decider)
    client = TestClient(create_app(store, Jobs(store, use_hatchet=False, ai=fake)))
    client.put(f"{WS}/files", json={"files": files})
    client.post(f"{WS}/releases", json={})
    token = client.post(f"{WS}/tokens").json()["token"]

    def send(body):
        r = client.post(f"{WS}/sessions", json=body, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code in (200, 201), r.text
        return client.get(f"{WS}/sessions/{r.json()['id']}").json()

    return SimpleNamespace(store=store, fake=fake, decider=decider, send=send, monkeypatch=monkeypatch)


def calls(fake, signature):
    return [c for c in fake.calls if c[1] is signature]


def asked(decider):
    return [q for _, qs in decider.requests for q in qs.values()]


def test_decisions_route_and_clear_rules_without_the_reasoning_model(env):
    s = env.send(session())
    checks = checks_by_id(s)
    assert checks[ALLERGEN]["applicable"] and checks[DELIVERY]["applicable"]
    a = checks[ALLERGEN]
    r = rules_by_key(a)
    assert [r[k]["status"] for k in ("step_1", "step_2", "never_1", "never_2", "warning_1")] == ["done", "done", "ok", "ok", "absent"]
    assert r["step_1"]["evidence"][0]["source"] == "transcript" and r["step_1"]["evidence"][0]["turn"] == 0  # the first agent utterance offered
    assert a["model"] == "fake-jev" and a["prompt_version"] == "decide-1"
    assert calls(env.fake, CheckRules) == []  # nothing flagged, nothing reviewed
    assert len(calls(env.fake, SplitRule)) == 9  # allergen (6 rules) and delivery (3), split once each
    actions = [q["instructions"].get("action", "") for q in asked(env.decider) if q["type"] == "noul" and isinstance(q["instructions"], dict)]
    assert any("tonys.com/allergens" in a for a in actions) and not any("{{menu_allergen_link}}" in a for a in actions)


def test_splits_are_stored_once_per_rule_version(env):
    env.send(session("one"))
    n = len(calls(env.fake, SplitRule))
    env.send(session("two"))
    assert len(calls(env.fake, SplitRule)) == n
    with Session(env.store.engine) as db:
        assert len(db.exec(select(RuleChecks)).all()) == n


def test_flagged_and_unsure_rules_go_to_the_reasoning_model(env):
    env.decider.responder = Decisions({ALLERGEN: 0.95}, {NEVER_SAFE: 0.9, "Name the specific allergen": 0.6})
    env.fake.responder = Generative({"never_1": ("violated", [(7, "totally safe for him")]), "step_2": ("done", [(3, "Got it, a peanut allergy.")])})
    s = env.send(session())
    a = checks_by_id(s)[ALLERGEN]
    r = rules_by_key(a)
    (_, _, inputs), = calls(env.fake, CheckRules)
    assert inputs["rules_to_check"] == ["step_2", "never_1"]  # the unsure step and the flagged never rule only
    assert r["never_1"]["status"] == "violated" and r["never_1"]["reason"] == "reviewed never_1" and r["never_1"]["evidence"][0]["turn"] == 7
    assert r["step_2"]["status"] == "done" and r["step_1"]["reason"].startswith("Decided")
    assert a["result"] == "deviated" and a["model"] == "fake-jev+fake/reasoning"


def test_without_review_the_decisions_stand(env):
    env.monkeypatch.setenv("SOPOPS_CHECK_REVIEW", "off")
    env.decider.responder = Decisions({ALLERGEN: 0.95}, {NEVER_SAFE: 0.9, "Name the specific allergen": 0.4})
    r = rules_by_key(checks_by_id(env.send(session()))[ALLERGEN])
    assert calls(env.fake, CheckRules) == []
    assert r["never_1"]["status"] == "violated" and r["never_1"]["evidence"][0]["turn"] == 0  # an agent utterance
    assert r["step_2"]["status"] == "skipped"  # before the last done step, so not "not reached"


def test_steps_after_the_last_done_step_were_not_reached(env):
    env.monkeypatch.setenv("SOPOPS_CHECK_REVIEW", "off")
    env.decider.responder = Decisions({ALLERGEN: 0.95, DELIVERY: 0.9}, {"estimated delivery time": 0.05})
    assert rules_by_key(checks_by_id(env.send(session()))[DELIVERY])["step_3"]["status"] == "not_reached"


def test_unsure_routing_is_answered_by_the_reasoning_model(env):
    env.decider.responder = Decisions({ALLERGEN: 0.5})
    generative = env.fake.responder

    def reasoning(role, signature, inputs):
        if signature.__name__ == "RouteSession":  # the same decisions, asked of the reasoning model
            return {k: {"value": k.startswith(field_name(f"p_{ALLERGEN}")), "confidence": 0.9, "probability": 0.9} for k in signature.output_fields}
        return generative(role, signature, inputs)

    env.fake.responder = reasoning
    s = env.send(session())
    assert checks_by_id(s)[ALLERGEN]["applicable"] and not checks_by_id(s)[DELIVERY]["applicable"]
    assert [c[0] for c in env.fake.calls if c[1].__name__ == "RouteSession"] == ["decide", "reasoning"]


def test_calibrated_thresholds_apply_while_their_question_is_unchanged(env):
    env.monkeypatch.setenv("SOPOPS_CHECK_REVIEW", "off")
    env.send(session("first"))
    step1 = next(q for _, qs in env.decider.requests for n, q in qs.items() if n == "step1_a0")
    current = {"step1_a0": calibration.question_hash({k: step1[k] for k in ("instructions", "criteria")})}
    calibration.save(env.store.engine, "demo", f"check:{ALLERGEN}", {"step1_a0": {"threshold": 0.99}}, {"step1_a0": "an-older-question"}, "fake")
    assert rules_by_key(checks_by_id(env.send(session("second")))[ALLERGEN])["step_1"]["status"] == "done"  # stale: ignored
    calibration.save(env.store.engine, "demo", f"check:{ALLERGEN}", {"step1_a0": {"threshold": 0.99, "criteria": {"x": 1}}}, current, "fake")
    assert calibration.load(env.store.engine, "demo", f"check:{ALLERGEN}", current) == {"step1_a0": {"threshold": 0.99}}
    assert rules_by_key(checks_by_id(env.send(session("third")))[ALLERGEN])["step_1"]["status"] == "skipped"  # 0.95 < 0.99


def test_a_generative_model_answers_decisions_and_locates_evidence(tmp_path, files):
    store = Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN))
    engine = FakeEngine(lambda outputs, text: {n: {"noul": 0.97 if n.endswith("_situation") or n.endswith("_responded") else 0.9} for n in outputs})
    gen = Models({"fast": "fake/fast", "reasoning": "fake/reasoning"}, jev=False)
    gen._clients["fake/fast"] = dspy.LM("fake/fast", engine=engine)  # decisions on the fast model
    fake = FakeModels(Generative(), None)
    fake.decision_native = lambda: False

    def predict(role, predictor, **inputs):
        if role == "decide":
            return gen.predict("decide", predictor, **inputs)
        return FakeModels.predict(fake, role, predictor, **inputs)

    fake.predict = predict
    client = TestClient(create_app(store, Jobs(store, use_hatchet=False, ai=fake)))
    client.put(f"{WS}/files", json={"files": files})
    client.post(f"{WS}/releases", json={})
    token = client.post(f"{WS}/tokens").json()["token"]
    sid = client.post(f"{WS}/sessions", json=session(), headers={"Authorization": f"Bearer {token}"}).json()["id"]
    a = checks_by_id(client.get(f"{WS}/sessions/{sid}").json())[ALLERGEN]
    r = rules_by_key(a)
    assert r["step_1"]["status"] == "done" and r["step_1"]["evidence"][0]["turn"] == 2  # from LocateEvidence
    assert engine.requests and gen.usage.by_role["decide"]["calls"] >= 2
    assert calls(fake, LocateEvidence)


def test_long_transcripts_are_cut_into_overlapping_segments():
    lines = [Line(i, f"U{i:03d}", "agent" if i % 2 else "user", "word " * 40) for i in range(600)]
    segs = segments({"name": "x"}, lines)
    assert len(segs) > 1 and all(len(s) <= 255 for s in segs)
    assert [x.turn for x in segs[1][:4]] == [x.turn for x in segs[0][-4:]]  # overlap
    assert {x.turn for s in segs for x in s} == set(range(600))


def test_patterns_are_assigned_with_a_choice():
    fake = FakeModels(None, FakeSystemOne(lambda state, qs: {k: {"m1": 0.7, "m2": 0.2, "other": 0.1} if k == "f1" else {"m2": 0.5, "other": 0.5} for k in qs}))
    group = decide.AssignGroup(rule="Step 1: ask", modes={"m1": "A", "m2": "B"}, findings={"f1": {"reason": "x"}, "f2": {"reason": "y"}})
    assert decide.assign(fake, [group]) == {"f1": "m1", "f2": "m2"}


def test_settings_from_env():
    cfg = DecisionConfig.from_env({"SOPOPS_DECISIONS": '{"never": [0.1, 0.8, 0.6], "review_statuses": ["violated"]}', "SOPOPS_CHECK_REVIEW": "off"})
    assert cfg.never == Band(0.1, 0.8, 0.6) and cfg.review_statuses == ("violated",) and cfg.review is False
    assert DecisionConfig.from_env({"SOPOPS_CHECK_MODE": "review"}).mode == "review"
    with pytest.raises(ValueError):
        DecisionConfig.parse({"nope": 1})


@pytest.mark.parametrize("env_vars,on", [({}, False), ({"TYPESAFE_API_KEY": "k"}, True), ({"TYPESAFE_API_KEY": "k", "SOPOPS_JEV": "off"}, False), ({"SOPOPS_JEV": "on"}, True)])
def test_jev_is_on_with_a_key_unless_turned_off(env_vars, on):
    assert jev_enabled(env_vars) is on


def test_models_count_tokens_and_cost_per_role():
    engine = FakeEngine(lambda outputs, text: {"checks": {"condition": None, "actions": ["The agent asks."], "forbidden": []}})
    models = Models({"fast": "fake/fast", "reasoning": "fake/reasoning"}, jev=False)
    models._clients["fake/reasoning"] = dspy.LM("fake/reasoning", engine=engine)
    pred = models.predict("reasoning", SplitRule, kind="step", rule="Ask", procedure="P")
    assert pred.checks.actions == ["The agent asks."]
    assert models.usage.calls == 1 and models.usage.by_role["reasoning"]["input_tokens"] > 0
    assert models.model("decide") == "fake/fast" and Models({"fast": "f"}, jev=True).model("decide") == "jev-1.13.0"


def _calibration_setup(env, probs, done_above):
    """Labeled sessions whose step 1 probability is `probs[i]`; step 1 counts as done only above `done_above`."""
    from sopops.pipeline.procedures import candidates

    env.send(session("seed"))
    rec = env.store.sessions.load("demo", env.send(session("seed2"))["id"])
    cand = candidates(env.store, rec)
    p = next(x for x in cand.procedures if x.id == ALLERGEN)
    splits = decide.rule_checks(env.fake, env.store.engine, "demo", p, cand.variables)
    step1 = next(r.id for r in p.rules if r.key == "step_1")
    by_text = {}

    def answer(state, qs):
        prob = by_text[state["inputs"]["transcript"][1]["text"]]
        return {k: (prob if k == "step1_a0" else Decisions({}, goal=0.2)(state, {k: q})[k]) for k, q in qs.items()}  # goal not reached

    decider = FakeSystemOne(answer)
    trainset = []
    for i, prob in enumerate(probs):
        turns = [{"role": "agent", "text": "Hi"}, {"role": "user", "text": f"case {i}"}, {"role": "agent", "text": "Any allergies?"}]
        by_text[f"case {i}"] = prob
        done = prob > done_above
        trainset.append(dspy.Example(turns=turns, deviated=not done, failing=set() if done else {step1}).with_inputs("turns"))
    return p, splits, cand, step1, decider, trainset


def test_calibration_fits_thresholds_offline_against_a_corpus_objective_and_stores_them(env):
    """Step 1 counts as done only when it's clearly there: the fit raises its threshold, from one answer per session."""
    from sopops.ai import programs

    p, splits, cand, step1, decider, trainset = _calibration_setup(env, [0.85, 0.8, 0.6, 0.55, 0.88, 0.58], 0.7)
    program = programs.CheckDecisions(p, splits, cand.variables, DecisionConfig(), decider)
    report = programs.calibrate(program, trainset, programs.check_objective(), env.store.engine, "demo", f"check:{ALLERGEN}", "fake-jev", num_threads=1)
    assert report["train_score"] > report["train_score_before"]
    assert len(decider.requests) == len(trainset)  # each session answered once; candidates are judged offline
    stored = calibration.load(env.store.engine, "demo", f"check:{ALLERGEN}", decide.question_hashes(program.decide))
    assert 0.6 < stored["step1_a0"]["threshold"] <= 0.8  # between the highest "no" and the lowest "yes"
    assert set(stored) <= {"step1_a0", "goal"}  # no other rule had labeled failures to fit on


def test_calibration_keeps_thresholds_within_bounds_and_needs_labeled_failures(env):
    from sopops.ai import programs

    p, splits, cand, step1, decider, trainset = _calibration_setup(env, [0.97, 0.98, 0.95, 0.99, 0.96, 0.93], 0.955)
    program = programs.CheckDecisions(p, splits, cand.variables, DecisionConfig(), decider)
    report = programs.calibrate(program, trainset, programs.check_objective(), env.store.engine, "demo", f"check:{ALLERGEN}", "fake-jev", num_threads=1)
    assert next(r for r in report["fitted"] if r["field"] == "step1_a0")["value"] <= programs.BOUNDS[1]
    few = trainset[:4]  # two labeled failures: below min_positives, so step 1 keeps its default
    program = programs.CheckDecisions(p, splits, cand.variables, DecisionConfig(), decider)
    report = programs.calibrate(program, few, programs.check_objective(), env.store.engine, "demo", f"check:{ALLERGEN}", "fake-jev", num_threads=1)
    row = next(r for r in report["fitted"] if r["field"] == "step1_a0")
    assert row["value"] == 0.5 and "labeled failures" in row["skipped"]


def test_a_calibrated_threshold_widens_the_review_band_but_never_narrows_it():
    band = Band(0.3, 0.9, 0.5)
    assert band.unsure(decide.Answer(0.85, True, 0, 0.5))
    assert band.unsure(decide.Answer(0.85, True, 0, 0.1))  # moved down: the default band still applies
    assert band.unsure(decide.Answer(0.2, False, 0, 0.1)) and not band.unsure(decide.Answer(0.2, False, 0, 0.5))  # widened down
    assert band.unsure(decide.Answer(0.95, True, 0, 0.6)) and not band.unsure(decide.Answer(0.95, True, 0, 0.5))  # widened up


def test_decision_requests_on_a_generative_model_are_built_once_and_match_dspy():
    from dspy.adapters.decision import resolve_adapter

    from sopops.ai import dspy_fixes
    from sopops.ai.signatures import ProcedureBrief, Utterance, action_question, check_signature, decision_predict

    names = [f"step{i}_a0" for i in range(12)]
    pred = decision_predict(check_signature(names), {n: action_question(f"The agent does {n}.") for n in names})
    adapter = resolve_adapter(dspy.LM("fake/fast"), dspy.ChatAdapter(), pred.signature, pred.fields)
    inputs = {"procedure": ProcedureBrief(name="x"), "transcript": [Utterance(id="U000", speaker="agent", text="hi")]}
    ours = adapter._prepare(pred.signature, [], inputs, {})
    theirs = dspy_fixes._original_prepare(adapter, pred.signature, [], inputs, {})
    chat = dspy.ChatAdapter()
    assert chat.format(ours["signature"], [], inputs) == chat.format(theirs["signature"], [], inputs)
    assert adapter._prepare(pred.signature, [], inputs, {})["signature"] is ours["signature"]  # built once
