"""SOP checks with a fake model: route, check, rule statuses, rollup, the deterministic tool check, exclusion,
idempotent re-runs, the API, and LiteLLM's JSON-mode fallback."""

import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, func, select

from conftest import SOPC_BIN, WS
from sopops import Sopc, Store, create_app
from sopops.jobs import Jobs
from sopops.llm import FakeLLM, LiteLLM, text_of
from sopops.models import Finding, ProcedureCheck, RuleResult
from sopops.pipeline import SessionRef, process_session
from sopops.pipeline.rollup import Judged, mark_out_of_order, rollup
from sopops.pipeline.transcript import verify_evidence

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


class Scenario:
    """A fake model: route decisions per procedure, and per procedure the rule judgments and goal."""

    def __init__(self, route, checks):
        self.route, self.checks = route, checks

    def __call__(self, role, messages, schema):
        if role == "fast":
            ids = re.findall(r'<procedure id="([^"]+)">', text_of(messages))
            return {"procedures": [{"procedure": pid, "reason": "fake", "status": self.route.get(pid, "not_applicable")} for pid in ids]}
        pid = re.search(r'<procedure id="([^"]+)">', text_of(messages)).group(1)
        spec = self.checks[pid]
        keys = re.findall(r"^- ((?:step|never|warning)_\d+): ", text_of(messages), re.M)
        rules = []
        for key in keys:
            default = {"step": "done", "never": "ok", "warning": "absent"}[key.split("_")[0]]
            status, ev = spec.get(key, (default, []))
            rules.append({"rule": key, "reason": f"fake {key}", "status": status, "evidence": [{"turn": t, "quote": q} for t, q in ev]})
        return {"rules": rules, "goal_reason": "fake", "goal_reached": spec.get("goal", "yes")}


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
def env(tmp_path, files):
    store = Store(f"sqlite:///{tmp_path / 'checks.db'}", Sopc(SOPC_BIN))
    fake = FakeLLM(BASE)
    client = TestClient(create_app(store, Jobs(store, use_hatchet=False, llm=fake)))
    client.put(f"{WS}/files", json={"files": files})
    client.post(f"{WS}/releases", json={})
    token = client.post(f"{WS}/ingest-token").json()["token"]

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
    assert allergen["prompt_version"] == "check-1" and allergen["model"] == "fake/reasoning"
    r = rules_by_key(allergen)
    assert [r[k]["status"] for k in ("step_1", "step_2", "step_3", "never_1", "never_2", "warning_1")] == ["done", "done", "done", "violated", "ok", "absent"]
    assert r["step_3"]["evidence"][0]["source"] == "tool_calls" and r["step_3"]["evidence"][0]["quote"].startswith("lookup_allergens(")
    assert r["never_1"]["evidence"] == [{"turn": 7, "quote": "totally safe for him", "source": "transcript"}]
    assert r["never_1"]["finding"] and not r["step_1"]["finding"]
    assert r["step_1"]["rule_id"] == "5cc0634f287a"  # sopc's stable id

    delivery = checks[DELIVERY]
    d = rules_by_key(delivery)
    # step 2's evidence (turn 4) comes before step 1's (turn 5): the fewest steps that explain it are flagged
    assert [d[k]["status"] for k in ("step_1", "step_2", "step_3")] == ["done", "out_of_order", "not_reached"]
    assert delivery["result"] == "partial" and delivery["steps_applicable"] == 3 and delivery["steps_done"] == 2

    with Session(env.store.engine) as db:
        findings = sorted((f.procedure_id, f.status) for f in db.exec(select(Finding)))
    assert findings == [(ALLERGEN, "violated"), (DELIVERY, "out_of_order")]
    # one route call, one check per applicable procedure; the transcript block is cached for the checks
    assert [c[0] for c in env.fake.calls] == ["fast", "reasoning", "reasoning"]
    check_msgs = env.fake.calls[1][1]
    assert check_msgs[1]["content"][0]["cache_control"] == {"type": "ephemeral"} and "<transcript>" in check_msgs[1]["content"][0]["text"]
    assert "allergen" in check_msgs[1]["content"][1]["text"] and "tonys.com/allergens" in check_msgs[1]["content"][1]["text"]  # variables filled


def test_required_tool_step_is_checked_against_tool_calls(env):
    # the model says the allergen lookup happened, but there's no such tool call
    env.fake.responder = Scenario(BASE.route | {DELIVERY: "not_applicable"}, {ALLERGEN: {"step_3": ("done", [(3, "Let me check the pepperoni.")])}})
    s = env.send(session("no-lookup", tool_calls=[{"name": "place_order", "arguments": {}, "offset_s": 9}]))
    a = checks_by_id(s)[ALLERGEN]
    step = rules_by_key(a)["step_3"]
    assert step["status"] == "skipped" and step["evidence"] == [] and "never called" in step["reason"]
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
    env.fake.responder = Scenario({ALLERGEN: "ended_before_finish", DELIVERY: "ended_before_start"}, {})
    s = env.send(session("hung-up"))
    assert s["status"] == "excluded" and s["result"] is None
    assert s["status_reason"] in ("The session ended before the procedure finished.", "The session ended before the procedure started.")
    checks = checks_by_id(s)
    assert checks[ALLERGEN]["applicable"] and checks[ALLERGEN]["excluded_reason"] == "The session ended before the procedure finished."
    assert checks[DELIVERY]["excluded_reason"] == "The session ended before the procedure started."
    assert [c[0] for c in env.fake.calls] == ["fast"]  # nothing checked


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


def test_partial_sessions_are_checked_but_stay_partial(env):
    s = env.send(session("cut-off", partial=True))
    assert s["status"] == "partial" and s["result"] == "deviated" and len(s["checks"]) == 3


def test_list_shows_and_filters_by_result(env):
    env.send(session("a"))
    env.fake.responder = Scenario({ALLERGEN: "applied"}, {ALLERGEN: {}})
    env.send(session("b"))
    rows = env.client.get(f"{WS}/sessions").json()["items"]
    assert {r["external_id"]: r["result"] for r in rows} == {"a": "deviated", "b": "followed"}
    only = env.client.get(f"{WS}/sessions", params={"result": "followed"}).json()["items"]
    assert [r["external_id"] for r in only] == ["b"]
    assert env.client.get(f"{WS}/sessions", params={"result": "nope"}).status_code == 422


def test_check_replies_must_cover_every_rule_once_with_fitting_statuses(env):
    from pydantic import ValidationError

    from sopops.pipeline.judge import check_schema, route_schema
    from sopops.pipeline.procedures import candidates

    s = env.send(session())
    rec = env.store.sessions.load("demo", s["id"])
    procs = {p.id: p for p in candidates(env.store, rec).procedures}
    schema = check_schema(procs[DELIVERY])
    ok = [{"rule": k, "reason": "r", "evidence": [], "status": "done"} for k in ("step_1", "step_2", "step_3")]
    schema.model_validate({"rules": ok, "goal_reason": "g", "goal_reached": "yes"})
    with pytest.raises(ValidationError, match="missing: step_3"):
        schema.model_validate({"rules": ok[:2], "goal_reason": "g", "goal_reached": "yes"})
    with pytest.raises(ValidationError, match="don't fit"):
        schema.model_validate({"rules": [*ok[:2], {**ok[2], "status": "violated"}], "goal_reason": "g", "goal_reached": "yes"})
    with pytest.raises(ValidationError):
        route_schema(list(procs.values())).model_validate({"procedures": [{"procedure": "nope", "reason": "r", "status": "applied"}]})


def test_model_failure_marks_the_session_failed(env):
    def boom(role, messages, schema):
        raise RuntimeError("provider is down")

    env.fake.responder = boom
    s = env.send(session("down"))
    assert s["status"] == "failed" and s["status_reason"] == "route: provider is down"


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


def test_litellm_json_mode_validates_and_retries_once(monkeypatch):
    import litellm
    from pydantic import BaseModel

    class Out(BaseModel):
        answer: int

    replies = iter(["not json", '```json\n{"answer": 42}\n```'])
    seen = []

    def completion(**kw):
        seen.append(kw)
        msg = SimpleNamespace(content=next(replies))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, prompt_tokens_details=None))

    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(litellm, "completion_cost", lambda completion_response: 0.001)
    llm = LiteLLM({"fast": "openai/local-model"})
    assert llm.complete("fast", [{"role": "user", "content": "?"}], Out) == Out(answer=42)
    assert seen[0]["response_format"] == {"type": "json_object"} and "JSON schema" in seen[0]["messages"][-1]["content"]
    assert "doesn't match" in seen[1]["messages"][-1]["content"]
    assert llm.usage.calls == 2 and llm.usage.retries == 1 and llm.usage.input_tokens == 20
    with pytest.raises(Exception, match="no model"):
        llm.complete("reasoning", [], Out)


def test_litellm_uses_native_structured_output_when_supported(monkeypatch):
    import litellm
    from pydantic import BaseModel

    class Out(BaseModel):
        answer: int

    seen = []

    def completion(**kw):
        seen.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer": 1}'))], usage=None)

    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    LiteLLM({"reasoning": "anthropic/x"}).complete("reasoning", [{"role": "user", "content": "?"}], Out)
    assert seen[0]["response_format"] is Out and len(seen[0]["messages"]) == 1
