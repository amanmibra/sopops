"""SOP checks with fake models: route, check (the reasoning model judging every rule), rule statuses, rollup, the
deterministic tool check, exclusion, idempotent re-runs and the API. The decision-first check is in test_decisions."""

import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from conftest import SOP_BIN, WS, fresh_db_url
from fakes import pattern_answer, pattern_reply
from fastapi.testclient import TestClient
from sqlmodel import Session, func, select

from sopops_server import Sop, Store, create_app
from sopops_server.ai.fakes import FakeModels, FakeSystemOne
from sopops_server.ai.signatures import CheckRules, field_name
from sopops_server.jobs import Jobs
from sopops_server.orm import Finding, ProcedureCheck, RuleResult
from sopops_server.pipeline.process import process_session
from sopops_server.pipeline.rollup import Judged, mark_out_of_order, rollup
from sopops_server.pipeline.transcript import verify_evidence
from sopops_server.pipeline.types import SessionRef

T0 = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
ALLERGEN, DELIVERY, LARGE = "allergen-check", "delivery-handling", "large-orders"

TURNS = [
    ("agent", "Tony's Pizza, how can I help?"),                                  # 0
    ("user", "A large pepperoni for delivery. My son has a peanut allergy."),     # 1
    ("agent", "Does anyone else in the order have a food allergy?"),             # 2
    ("agent", "Got it, a peanut allergy. Let me check the pepperoni."),          # 3
    ("agent", "That's inside our delivery zone."),                              # 4
    ("agent", "What's the full delivery address?"),                             # 5
    ("user", "221 Mercer Street."),                                             # 6
    ("agent", "The pepperoni is totally safe for him."),                        # 7
]


def session(external_id="s1", tool_calls=None, turns=TURNS, **kw):
    return {
        "platform": "livekit",
        "external_id": external_id,
        "agent": "tonys-pizza",
        "started_at": T0.isoformat(),
        "ended_at": (T0 + timedelta(seconds=60)).isoformat(),
        "turns": [{"role": r, "text": t, "offset_s": i * 2.0} for i, (r, t) in enumerate(turns)],
        "tool_calls": tool_calls if tool_calls is not None else [{"name": "lookup_allergens", "arguments": {"item": "pepperoni"}, "result": {"peanut": False}, "offset_s": 7}],
        **kw,
    }


ROUTE_ANSWERS = {"applied": (0.95, 0.95), "not_applicable": (0.03, 0.03), "ended_before_finish": (0.95, 0.04)}


class Scenario:
    """Fake models: route decisions per procedure, and per procedure the rule judgments and goal (the reasoning model
    judges every rule: the tests here set SOPOPS_CHECK_MODE=review). Pattern calls get the pattern fakes."""

    def __init__(self, route, checks):
        self.route, self.checks = route, checks

    def __call__(self, role, signature, inputs):
        if signature is not CheckRules:
            return pattern_reply(role, signature, inputs)
        pid = re.search(r'<procedure id="([^"]+)">', inputs["procedure"]).group(1)
        spec = self.checks[pid]
        rules = []
        for key in inputs["rules_to_check"]:
            default = {"step": "done", "never": "ok", "warning": "absent"}[key.split("_")[0]]
            status, ev = spec.get(key, (default, []))
            rules.append({"rule": key, "reason": f"fake {key}", "status": status, "evidence": [{"turn": t, "quote": q} for t, q in ev]})
        return {"rules": rules, "goal_reason": "fake", "goal_reached": spec.get("goal", "yes")}

    def decide(self, state, questions):
        if "findings" in state["inputs"]:
            return pattern_answer(state, questions)
        out = {}
        for pid in (ALLERGEN, DELIVERY, LARGE, "reservations"):
            situation, responded = ROUTE_ANSWERS[self.route.get(pid, "not_applicable")]
            out[f"{field_name('p_' + pid)}_situation"], out[f"{field_name('p_' + pid)}_responded"] = situation, responded
        return {k: out[k] for k in questions}


def fake_models(scenario):
    """Generative calls go to the scenario; decisions to its `decide` (whichever scenario the test sets later)."""
    fake = FakeModels(scenario)
    fake.decider = FakeSystemOne(lambda state, qs: fake.responder.decide(state, qs))
    return fake


BASE = Scenario(
    route={ALLERGEN: "applied", DELIVERY: "applied", LARGE: "not_applicable"},
    checks={
        ALLERGEN: {
            "step_1": ("done", [(2, "Does anyone else in the order have a food allergy?")]),
            "step_2": ("done", [(3, "Got it, a peanut allergy.")]),
            "step_3": ("skipped", []),  # the model missed it; the tool-call log says it happened
            "never_1": ("violated", [(7, "totally safe for him")]),
        },
        DELIVERY: {
            "step_1": ("done", [(5, "What's the full delivery address?")]),
            "step_2": ("done", [(4, "That's inside our delivery zone.")]),
            "step_3": ("not_reached", []),
        },
    },
)


@pytest.fixture
def env(tmp_path, files, monkeypatch):
    monkeypatch.setenv("SOPOPS_CHECK_MODE", "review")
    store = Store(fresh_db_url(tmp_path), Sop(SOP_BIN))
    fake = fake_models(BASE)
    client = TestClient(create_app(store, Jobs(store, use_hatchet=False, ai=fake)))
    client.put(f"{WS}/files", json={"files": files})
    client.post(f"{WS}/releases", json={})
    token = client.post(f"{WS}/tokens").json()["token"]

    def send(body):
        r = client.post(f"{WS}/sessions", json=body, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code in (200, 201), r.text
        return client.get(f"{WS}/sessions/{r.json()['id']}").json()

    return SimpleNamespace(store=store, fake=fake, client=client, send=send)


def checks_by_id(s):
    return {c["procedure_id"]: c for c in s["checks"]}


def rules_by_key(check):
    return {f"{r['kind']}_{r['index']}": r for r in check["rules"]}


def test_route_and_check_statuses_and_rollup(env):
    s = env.send(session())
    assert s["status"] == "checked" and s["status_reason"] is None and s["result"] == "deviated"
    checks = checks_by_id(s)
    assert set(checks) == {ALLERGEN, DELIVERY, LARGE}
    assert checks[LARGE]["applicable"] is False and checks[LARGE]["result"] is None and checks[LARGE]["rules"] == []

    allergen = checks[ALLERGEN]
    assert allergen["result"] == "deviated" and allergen["goal_reached"] is True
    assert allergen["prompt_version"] == "check-6" and allergen["model"] == "fake/reasoning"
    r = rules_by_key(allergen)
    assert [r[k]["status"] for k in ("step_1", "step_2", "step_3", "never_1", "never_2", "warning_1")] == ["done", "done", "done", "violated", "ok", "absent"]
    assert r["step_3"]["evidence"][0]["source"] == "tool_calls" and r["step_3"]["evidence"][0]["quote"].startswith("lookup_allergens(")
    assert r["never_1"]["evidence"] == [{"turn": 7, "quote": "totally safe for him", "source": "transcript"}]
    assert r["never_1"]["finding"] and not r["step_1"]["finding"]
    assert r["step_1"]["rule_id"] == "5cc0634f287a"  # sop's stable id

    delivery = checks[DELIVERY]
    d = rules_by_key(delivery)
    # step 2's evidence (turn 4) comes before step 1's (turn 5): the fewest steps that explain it are flagged
    assert [d[k]["status"] for k in ("step_1", "step_2", "step_3")] == ["done", "out_of_order", "not_reached"]
    assert delivery["result"] == "partial" and delivery["steps_applicable"] == 3 and delivery["steps_done"] == 2

    with Session(env.store.engine) as db:
        findings = sorted((f.procedure_id, f.status) for f in db.exec(select(Finding)))
    assert findings == [(ALLERGEN, "violated"), (DELIVERY, "out_of_order")]
    # one route decision, one check per applicable procedure
    assert [c[0] for c in env.fake.calls if c[1].__name__ in ("RouteSession", "CheckRules")] == ["decide", "reasoning", "reasoning"]
    check = next(c[2] for c in env.fake.calls if c[1] is CheckRules)
    assert "<transcript>" in check["transcript"] and "tonys.com/allergens" in check["procedure"]  # variables filled


def test_required_tool_step_is_checked_against_tool_calls(env):
    # the model says the allergen lookup happened, but there's no such tool call
    env.fake.responder = Scenario(BASE.route | {DELIVERY: "not_applicable"}, {ALLERGEN: {"step_3": ("done", [(3, "Let me check the pepperoni.")])}})
    s = env.send(session("no-lookup", tool_calls=[{"name": "place_order", "arguments": {}, "offset_s": 9}]))
    a = checks_by_id(s)[ALLERGEN]
    step = rules_by_key(a)["step_3"]
    assert step["status"] == "skipped" and step["evidence"] == [] and "never called" in step["reason"]
    assert step["reason"].startswith("fake step_3")  # the checker's reading is kept (clustering needs it), the tool fact added
    assert a["result"] == "deviated"  # a required step skipped, even with the goal reached


def test_without_tool_data_the_transcript_decides(env):
    env.fake.responder = Scenario(BASE.route | {DELIVERY: "not_applicable"}, {ALLERGEN: {"step_3": ("done", [(3, "Let me check the pepperoni.")])}})
    s = env.send(session("no-tools", tool_calls=[]))
    a = checks_by_id(s)[ALLERGEN]
    step = rules_by_key(a)["step_3"]
    assert step["status"] == "done" and step["evidence"][0]["source"] == "transcript"
    assert a["result"] == "followed" and s["result"] == "followed"


@pytest.mark.parametrize("goal,expected", [("yes", "partial"), ("no", "deviated"), ("unclear", "deviated")])
def test_leniency_for_a_skipped_step_that_isnt_required(env, goal, expected):
    env.fake.responder = Scenario(BASE.route | {DELIVERY: "not_applicable"}, {ALLERGEN: {"step_2": ("skipped", []), "goal": goal}})
    s = env.send(session(f"lenient-{goal}"))
    a = checks_by_id(s)[ALLERGEN]
    step = rules_by_key(a)["step_2"]
    assert step["status"] == "skipped" and step["finding"]
    assert a["result"] == expected
    assert ("Lenient" in step["reason"]) == (goal == "yes")


def test_a_session_that_ended_early_is_excluded(env):
    env.fake.responder = Scenario({ALLERGEN: "ended_before_finish"}, {})
    s = env.send(session("hung-up"))
    assert s["status"] == "excluded" and s["result"] is None
    assert s["status_reason"] == "The session ended before the procedure finished."
    checks = checks_by_id(s)
    assert checks[ALLERGEN]["applicable"] and checks[ALLERGEN]["excluded_reason"] == "The session ended before the procedure finished."
    assert [c[0] for c in env.fake.calls] == ["decide"]  # nothing checked
    silent = env.send(session("silent", turns=[("agent", "Tony's Pizza, how can I help?")]))
    assert silent["status_reason"] == "The session ended before the procedure started."


def test_no_procedure_applied(env):
    env.fake.responder = Scenario({}, {})
    s = env.send(session("wrong-number"))
    assert s["status"] == "excluded" and s["status_reason"] == "No procedure applied." and not any(c["applicable"] for c in s["checks"])


def test_rerunning_is_idempotent_and_a_resend_replaces_results(env):
    s = env.send(session())
    with Session(env.store.engine) as db:
        row = db.exec(select(ProcedureCheck).where(ProcedureCheck.session_id == s["id"])).first()
        revision = row.revision

    def counts():
        with Session(env.store.engine) as db:
            return tuple(db.exec(select(func.count()).select_from(t)).one() for t in (ProcedureCheck, RuleResult, Finding))

    before = counts()
    ref = SessionRef(workspace_id="demo", session_id=s["id"], revision=revision)
    again = process_session(env.store, ref, env.fake)
    assert again.status == "checked" and again.result == "deviated" and counts() == before

    # a stale run (an older revision) writes nothing
    stale = process_session(env.store, SessionRef(workspace_id="demo", session_id=s["id"], revision="0" * 64), env.fake)
    assert stale.skipped and counts() == before

    # re-sent with new content: the old results are replaced, not added to
    env.fake.responder = Scenario({ALLERGEN: "applied"}, {ALLERGEN: {}})
    s2 = env.send(session(turns=TURNS[:7]))
    assert s2["id"] == s["id"] and s2["result"] == "followed"
    assert counts() == (3, 6, 0)


def test_incomplete_sessions_are_checked_but_stay_incomplete(env):
    s = env.send(session("cut-off", partial=True))
    assert s["status"] == "incomplete" and s["result"] == "deviated" and len(s["checks"]) == 3


def test_list_shows_and_filters_by_result(env):
    env.send(session("a"))
    env.fake.responder = Scenario({ALLERGEN: "applied"}, {ALLERGEN: {}})
    env.send(session("b"))
    rows = env.client.get(f"{WS}/sessions").json()["items"]
    assert {r["external_id"]: r["result"] for r in rows} == {"a": "deviated", "b": "followed"}
    only = env.client.get(f"{WS}/sessions", params={"result": "followed"}).json()["items"]
    assert [r["external_id"] for r in only] == ["b"]
    assert env.client.get(f"{WS}/sessions", params={"result": "nope"}).status_code == 422


class Partial(Scenario):
    """Leaves out step_2 and gives never_1 a status that doesn't fit a never rule."""

    def __call__(self, role, signature, inputs):
        reply = super().__call__(role, signature, inputs)
        if signature is CheckRules:
            reply["rules"] = [r if r["rule"] != "never_1" else {**r, "status": "done"} for r in reply["rules"] if r["rule"] != "step_2"]
        return reply


def test_a_review_that_leaves_out_rules_or_misfits_a_status_keeps_the_defaults(env):
    env.fake.responder = Partial(BASE.route, BASE.checks)
    r = rules_by_key(checks_by_id(env.send(session("partial-reply")))[ALLERGEN])
    assert r["step_2"]["status"] == "not_applicable" and r["step_2"]["reason"] == "Not judged."
    assert r["never_1"]["status"] == "ok"  # "done" doesn't fit a never rule


def test_model_failure_marks_the_session_failed(env):
    def boom(state, questions):
        raise RuntimeError("provider is down")

    env.fake.decider.responder = boom
    s = env.send(session("down"))
    assert s["status"] == "failed" and s["status_reason"].startswith("route: ") and "provider is down" in s["status_reason"]


# --- pure pieces --------------------------------------------------------------------


def J(kind, index, status, turn=None, required=False):
    return Judged(f"{kind}{index}", kind, index, status, [{"turn": turn, "quote": "q"}] if turn is not None else [], "r", required)


def test_out_of_order_flags_the_fewest_steps():
    steps = [J("step", 1, "done", 1), J("step", 2, "done", 9), J("step", 3, "done", 3), J("step", 4, "done", 5), J("step", 5, "skipped")]
    mark_out_of_order(steps)
    assert [s.status for s in steps] == ["done", "out_of_order", "done", "done", "skipped"]


def test_rollup_rules():
    assert rollup([J("step", 1, "not_applicable"), J("never", 1, "ok")], True).result == "not_measurable"
    assert rollup([J("step", 1, "not_applicable"), J("never", 1, "violated")], True).result == "deviated"
    assert rollup([J("step", 1, "done")], True, agent_spoke=False).result == "not_measurable"
    assert rollup([J("step", 1, "done"), J("warning", 1, "present")], False).result == "followed"  # warnings never count
    assert rollup([J("step", 1, "done"), J("step", 2, "not_reached")], None).result == "partial"
    assert rollup([J("step", 1, "skipped", required=True)], True).result == "deviated"
    assert rollup([J("step", 1, "skipped")], True).result == "partial"
    assert rollup([J("step", 1, "skipped")], None).result == "deviated"


def test_evidence_is_checked_against_the_transcript():
    turns = [{"role": "agent", "text": "Hello there."}, {"role": "agent", "text": "Your total is $12, ready at six."}]
    got = verify_evidence([{"turn": 0, "quote": "your total is $12"}, {"turn": 1, "quote": "I never said this"}, {"turn": 9, "quote": "Hello"}], turns)
    assert got == [{"turn": 1, "quote": "your total is $12", "source": "transcript"}, {"turn": 0, "quote": "Hello", "source": "transcript"}]


# --- OR options ---------------------------------------------------------------------------------------------------

def _or_procedure():
    from sopops_server.pipeline.procedures import _procedure

    sop = {
        "id": "booking", "name": "Booking",
        "procedureSteps": [
            "Collect the details",
            {"text": "If they want to cancel, cancel it", "tool": "cancel_booking", "required": True,
             "or": [{"text": "If they want to change it, change it", "tool": "change_booking", "required": False}, "If they're late, tell the store"]},
            "Close the call",
        ],
        "rules": [
            {"id": "s1", "kind": "step", "index": 1, "text": "Collect the details"},
            {"id": "s2", "kind": "step", "index": 2, "text": "If they want to cancel, cancel it OR If they want to change it, change it OR If they're late, tell the store"},
            {"id": "o1", "kind": "option", "index": 2, "text": "If they want to cancel, cancel it", "option": 1, "of": "s2"},
            {"id": "o2", "kind": "option", "index": 2, "text": "If they want to change it, change it", "option": 2, "of": "s2"},
            {"id": "o3", "kind": "option", "index": 2, "text": "If they're late, tell the store", "option": 3, "of": "s2"},
            {"id": "s3", "kind": "step", "index": 3, "text": "Close the call"},
        ],
    }
    return _procedure(sop, {}, 1)


def test_or_options_load_with_their_own_tools_and_are_judged_as_steps():
    from sopops_server.pipeline.transcript import render_procedure

    p = _or_procedure()
    by = {r.id: r for r in p.rules}
    assert (by["s2"].tool, by["s2"].required) == (None, False)  # a step with options has no tool of its own
    assert [(by[o].tool, by[o].required, by[o].option, by[o].of) for o in ("o1", "o2", "o3")] == [
        ("cancel_booking", True, 1, "s2"), ("change_booking", False, 2, "s2"), (None, False, 3, "s2")]
    judged = p.judged()
    assert [(r.id, r.kind, r.key) for r in judged.rules] == [
        ("s1", "step", "step_1"), ("o1", "step", "step_2_or_1"), ("o2", "step", "step_2_or_2"), ("o3", "step", "step_2_or_3"), ("s3", "step", "step_3")]
    text = render_procedure(judged, {})
    assert "- step_2_or_1: If they want to cancel, cancel it (tool: cancel_booking, required)\n- step_2_or_2: OR If they want to change it" in text


def test_or_step_takes_its_status_from_its_options():
    from sopops_server.pipeline.rollup import combine_options

    def O(n, status, evidence=None, required=False):
        return Judged(f"o{n}", "option", 2, status, evidence or [], f"r{n}", required)

    g = combine_options("s2", [O(1, "not_applicable"), O(2, "done", [{"turn": 4, "quote": "q"}]), O(3, "not_applicable")])
    assert (g.kind, g.index, g.status, g.evidence[0]["turn"]) == ("step", 2, "done", 4) and g.reason.startswith("Option 2:")
    assert combine_options("s2", [O(1, "done"), O(2, "done"), O(3, "not_applicable")]).reason.endswith("Options 1, 2 were all done; only one should be.")
    g = combine_options("s2", [O(1, "skipped", required=True), O(2, "not_applicable"), O(3, "not_applicable")])
    assert (g.status, g.required) == ("skipped", True)
    # the wrong option: the agent called another option's tool instead, even where the model read the session as ending
    opts = [O(1, "not_reached", required=True), O(2, "not_applicable", [{"turn": 6, "quote": "change_booking(id=1)", "source": "tool_calls"}]), O(3, "not_applicable")]
    g = combine_options("s2", opts)
    assert g.status == "skipped" and opts[0].status == "skipped" and "The agent did option 2 instead: change_booking(id=1)." in g.reason
    assert combine_options("s2", [O(1, "not_reached"), O(2, "not_applicable"), O(3, "not_reached")]).status == "not_reached"
    assert combine_options("s2", [O(1, "not_applicable"), O(2, "not_applicable"), O(3, "not_applicable")]).status == "not_applicable"


def test_a_tool_called_for_a_step_that_didnt_apply_keeps_it_not_applicable():
    from sopops_server.pipeline.rollup import apply_tool_calls

    steps = [J("step", 1, "not_applicable"), J("step", 2, "skipped")]
    calls = [{"name": "change_booking", "arguments": {"id": 1}}, {"name": "cancel_booking", "arguments": {"id": 1}}]
    apply_tool_calls(steps, {"step1": "change_booking", "step2": "cancel_booking"}, calls, [])
    assert steps[0].status == "not_applicable" and steps[0].evidence[0]["source"] == "tool_calls" and "Called change_booking anyway." in steps[0].reason
    assert steps[1].status == "done"


def test_or_options_order_and_rollup_count_the_step_once():
    from sopops_server.pipeline.process import with_options

    p = _or_procedure()
    judged = [J("step", 1, "done", 1), Judged("o1", "step", 2, "not_applicable", [], "r"), Judged("o2", "step", 2, "done", [{"turn": 3, "quote": "q"}], "r"),
              Judged("o3", "step", 2, "not_applicable", [], "r"), J("step", 3, "done", 2)]
    judged[0].rule_id, judged[4].rule_id = "s1", "s3"
    rules = with_options(p, judged)
    assert [(r.rule_id, r.kind, r.status) for r in rules] == [
        ("s1", "step", "done"), ("s2", "step", "done"), ("o1", "option", "not_applicable"), ("o2", "option", "done"), ("o3", "option", "not_applicable"), ("s3", "step", "done")]
    mark_out_of_order([r for r in rules if r.kind == "step"])
    # step 2 (turn 3) and step 3 (turn 2) are out of order with each other: one of them is flagged, the options aren't
    assert [r.status for r in rules if r.kind == "step"].count("out_of_order") == 1
    assert [r.status for r in rules if r.kind == "option"] == ["not_applicable", "done", "not_applicable"]
    roll = rollup(rules, True)
    assert (roll.result, roll.steps_applicable, roll.steps_done) == ("partial", 3, 3)


OR_STEP = """  - text: Check each item the customer ordered against {{menu_allergen_link}}
    tool: lookup_allergens
    required: true
    or:
      - text: If they only ordered drinks, check the drinks list
        tool: drinks_lookup
"""


@pytest.fixture
def or_env(tmp_path, files, monkeypatch):
    """The allergen check with its tool step as the first of two OR options."""
    monkeypatch.setenv("SOPOPS_CHECK_MODE", "review")
    path = "procedures/allergen-check.yaml"
    old = "  - text: Check each item the customer ordered against {{menu_allergen_link}}\n    tool: lookup_allergens\n    required: true\n"
    assert old in files[path]
    files = {**files, path: files[path].replace(old, OR_STEP)}
    store = Store(fresh_db_url(tmp_path), Sop(SOP_BIN))
    fake = fake_models(BASE)
    client = TestClient(create_app(store, Jobs(store, use_hatchet=False, ai=fake)))
    r = client.put(f"{WS}/files", json={"files": files})
    if r.status_code != 200:
        pytest.skip(f"needs a sop with OR options (v0.0.18+): {r.text[:200]}")
    client.post(f"{WS}/releases", json={})
    token = client.post(f"{WS}/tokens").json()["token"]

    def send(body):
        r = client.post(f"{WS}/sessions", json=body, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code in (200, 201), r.text
        return client.get(f"{WS}/sessions/{r.json()['id']}").json()

    return SimpleNamespace(fake=fake, send=send)


def or_rules(s):
    return {(r["kind"], r["index"], r["option"]): r for r in checks_by_id(s)[ALLERGEN]["rules"]}


def test_or_step_end_to_end(or_env):
    or_env.fake.responder = Scenario(BASE.route, {**BASE.checks, ALLERGEN: {
        "step_1": ("done", [(2, "Does anyone else in the order have a food allergy?")]),
        "step_2": ("done", [(3, "Got it, a peanut allergy.")]),
        "step_3_or_1": ("skipped", []),  # the model missed it; the tool-call log says it happened
        "step_3_or_2": ("not_applicable", []),
    }})
    s = or_env.send(session())
    rules = or_rules(s)
    step, first, second = rules[("step", 3, None)], rules[("option", 3, 1)], rules[("option", 3, 2)]
    assert (step["status"], first["status"], second["status"]) == ("done", "done", "not_applicable")
    assert first["option_of"] == second["option_of"] == step["rule_id"] and not step["finding"]
    assert [(r["kind"], r["option"]) for r in checks_by_id(s)[ALLERGEN]["rules"]][:5] == [("step", None), ("step", None), ("step", None), ("option", 1), ("option", 2)]
    check = checks_by_id(s)[ALLERGEN]
    assert (check["steps_applicable"], check["steps_done"]) == (3, 3)  # the OR step counts once


def test_or_step_wrong_option(or_env):
    or_env.fake.responder = Scenario(BASE.route, {**BASE.checks, ALLERGEN: {
        "step_1": ("done", [(2, "Does anyone else in the order have a food allergy?")]),
        "step_2": ("done", [(3, "Got it, a peanut allergy.")]),
        "step_3_or_1": ("skipped", []),
        "step_3_or_2": ("not_applicable", []),
        "never_1": ("ok", []),
    }})
    s = or_env.send(session(tool_calls=[{"name": "drinks_lookup", "arguments": {"item": "cola"}, "offset_s": 7}]))
    rules = or_rules(s)
    step, first, second = rules[("step", 3, None)], rules[("option", 3, 1)], rules[("option", 3, 2)]
    assert (step["status"], first["status"], second["status"]) == ("skipped", "skipped", "not_applicable")
    assert "The agent did option 2 instead: drinks_lookup(" in step["reason"]
    assert first["finding"] and not step["finding"] and not second["finding"]
    assert checks_by_id(s)[ALLERGEN]["result"] == "deviated"  # option 1 is required
