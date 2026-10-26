"""Labels on rules with a fake model: the right status on an override, corrections on any rule (a wrong status, a
mistake the checker missed), spot checks (the sample, close calls first, answers), calibration reading the corrected
statuses (and unchanged without them), and model-call listeners."""

from collections import defaultdict
from types import SimpleNamespace

import dspy
import pytest
from conftest import WS
from sqlmodel import Session, select
from test_review import ALLERGEN, WINDOW, env, rule_pattern, session, tiles, verdict  # noqa: F401  (env is a fixture)

from sopops_server.ai import tracing
from datetime import datetime, timedelta, timezone

from sopops_server.labels import CLOSE_BAND, weight
from sopops_server.orm import Finding, FindingOverride, Pattern, ProcedureCheck, RuleLabel, RuleResult, SessionRecord, SpotCheck
from sopops_server.pipeline.rollup import Judged, rollup
from sopops_server.verdicts import LENIENT_MARK, PASS_STATUS, SUPPRESSING, Verdicts, _published, _utc, effective


def rules_of(env, sid):
    return session(env, sid)["checks"][0]["rules"]


def rule(env, sid, kind, index):
    return next(r for r in rules_of(env, sid) if r["kind"] == kind and r["index"] == index)


def label(env, sid, r, status, **kw):
    return env.client.post(f"{WS}/sessions/{sid}/rules/{ALLERGEN}/{r['rule_id']}/label", json={"status": status, **kw})


def test_an_override_can_say_what_actually_happened(env):
    sid = env.send("A")
    never = rule(env, sid, "never", 1)
    url = f"{WS}/findings/{never['finding_id']}/override"
    # the status has to fit the rule's kind and the call: a wrong finding isn't a finding status, a right one is
    assert env.client.post(url, json={"correct": False, "status": "done"}).status_code == 422
    assert env.client.post(url, json={"correct": False, "status": "violated"}).status_code == 422
    assert env.client.post(url, json={"correct": True, "status": "ok"}).status_code == 422
    r = env.client.post(url, json={"correct": False, "status": "ok", "reviewer": "qa", "note": "No promise was made."})
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["suppressed"]) == ("ok", True)
    got = rule(env, sid, "never", 1)
    assert got["override"] is False and got["suppressed"] is True
    assert {k: got["correction"][k] for k in ("status", "checked_status", "source", "reviewer", "note")} == {
        "status": "ok", "checked_status": None, "source": "override", "reviewer": "qa", "note": "No promise was made."}
    with Session(env.store.engine) as db:
        assert db.exec(select(FindingOverride.status)).all() == ["ok"]
    # without a status, an override is what it was: no correction shown
    env.client.post(url, json={"correct": True})
    assert rule(env, sid, "never", 1)["correction"] is None and rule(env, sid, "never", 1)["suppressed"] is False


def test_correcting_a_rule_without_a_finding_keeps_insights_as_the_checker_found(env):
    sid = env.send("A")
    before = tiles(env)
    step = rule(env, sid, "step", 1)
    assert (step["status"], step["finding"], step["correction"]) == ("done", False, None)
    assert label(env, sid, step, "violated").status_code == 422  # not a step's status
    assert env.client.post(f"{WS}/sessions/{sid}/rules/{ALLERGEN}/nope/label", json={"status": "skipped"}).status_code == 404
    r = label(env, sid, step, "skipped", reviewer="qa", note="Never asked about allergies.")
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["checked_status"], out["status"], out["source"], out["missed"], out["suppressed"]) == ("done", "skipped", "correction", True, None)
    got = rule(env, sid, "step", 1)
    # stored and shown, but no finding: the session's result, its patterns and Insights stay what the checker found
    assert got["status"] == "done" and got["finding"] is False and got["correction"]["status"] == "skipped" and got["correction"]["source"] == "correction"
    assert got["correction"]["reviewer"] == "qa" and got["correction"]["checked_status"] == "done"
    assert session(env, sid)["result"] == "deviated" and tiles(env) == before
    with Session(env.store.engine) as db:
        assert len(db.exec(select(Finding).where(Finding.session_id == sid)).all()) == 1


def test_a_wrong_status_on_a_finding_suppresses_it_like_an_override(env):
    sid = env.send("A")
    never = rule(env, sid, "never", 1)
    assert never["finding"] and session(env, sid)["result"] == "deviated"
    out = label(env, sid, never, "ok").json()
    assert (out["missed"], out["suppressed"]) == (False, True)
    assert session(env, sid)["result"] == "followed" and tiles(env)["suppressed"] == 1
    assert rule(env, sid, "never", 1)["override"] is False
    # the latest call wins: an override after it brings the finding back, and a later label again
    env.client.post(f"{WS}/findings/{never['finding_id']}/override", json={"correct": True})
    assert session(env, sid)["result"] == "deviated"
    label(env, sid, never, "ok")
    assert session(env, sid)["result"] == "followed"
    # a label outlives a re-check
    env.store.verdicts.refresh("demo", sessions=[sid])
    with Session(env.store.engine) as db:
        assert db.exec(select(Finding.suppressed).where(Finding.session_id == sid)).all() == [True]
    # a status that is a finding says it's right, which overrides a checker_wrong verdict for this finding
    verdict(env, rule_pattern(env)["pattern_id"], "checker_wrong")
    label(env, sid, never, "violated")
    assert session(env, sid)["result"] == "deviated"


def spot(env, **params):
    r = env.client.get(f"{WS}/spot-checks", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def answer(env, item, status=None, **kw):
    r = env.client.post(f"{WS}/sessions/{item['session_id']}/rules/{ALLERGEN}/{item['rule_id']}/label", json={"status": status or item["status"], "source": "spot_check", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_spot_checks_come_in_batches(env):
    ids = [env.send("A") for _ in range(4)]  # 4 sessions x 5 rule results with no finding (3 steps, never 2, the warning sign)
    assert env.client.get(f"{WS}/spot-checks/settings").json() == {"batch": 25, "default": True}
    assert env.client.put(f"{WS}/spot-checks/settings", json={"batch": 0}).status_code == 422
    assert env.client.put(f"{WS}/spot-checks/settings", json={"batch": 6}).json() == {"batch": 6, "default": False}
    q = spot(env)  # the first batch is drawn on the first visit
    assert (q["size"], q["answered"], q["corrections"], q["done"], q["default_batch"]) == (6, 0, 0, False, 6) and len(q["items"]) == 6
    item = q["items"][0]
    assert item["status"] in ("done", "ok", "absent") and item["why"] == ["sample"] and item["procedure_name"] and item["agent_id"] == "tonys-pizza"
    assert item["statuses"] == {"step": ["done", "out_of_order", "skipped", "not_reached", "not_applicable"], "never": ["violated", "ok"],
                                "warning": ["present", "unheeded", "absent"]}[item["kind"]]
    assert "{{" not in item["text"] and (item["answered"], item["answer"]) == (False, None)
    assert spot(env)["batch_id"] == q["batch_id"]  # no new batch on its own
    out = answer(env, item)
    assert out["source"] == "spot_check" and out["missed"] is False
    answer(env, q["items"][1], "skipped" if q["items"][1]["kind"] == "step" else {"never": "violated", "warning": "unheeded"}[q["items"][1]["kind"]])
    q = spot(env)
    assert (q["size"], q["answered"], q["corrections"], q["done"]) == (6, 2, 1, False)
    assert next(x for x in q["items"] if x["spot_check_id"] == item["spot_check_id"])["answer"] == item["status"]
    for x in q["items"]:
        if not x["answered"]:
            answer(env, x)
    q = spot(env)
    assert (q["answered"], q["done"]) == (6, True)
    # "give me more": a new batch, nothing drawn twice; only 14 are left
    r = env.client.post(f"{WS}/spot-checks/batches", json={"size": 25})
    assert r.status_code == 200, r.text
    nxt = r.json()
    assert nxt["batch_id"] != q["batch_id"] and nxt["size"] == 14 and not nxt["done"]
    assert not {(x["session_id"], x["rule_id"]) for x in nxt["items"]} & {(x["session_id"], x["rule_id"]) for x in q["items"]}
    assert env.client.post(f"{WS}/spot-checks/batches", json={"size": 500}).status_code == 422
    assert ids


def test_spot_check_filters_sort_and_expiry(env):
    a, b = env.send("A"), env.send("A")
    q = spot(env)
    assert {x["session_id"] for x in spot(env, agent="tonys-pizza")["items"]} == {a, b} and spot(env, agent="nobody")["items"] == []
    assert len(spot(env, procedure=ALLERGEN)["items"]) == 10 and spot(env, procedure="nope")["items"] == []
    step1 = rule(env, a, "step", 1)["rule_id"]
    assert {x["rule_id"] for x in spot(env, rule=step1)["items"]} == {step1}
    assert len(spot(env, why="sample")["items"]) == 10 and spot(env, why="close_call")["items"] == []
    late = rules_of(env, b) and env.client.get(f"{WS}/sessions/{b}").json()["started_at"]
    assert {x["session_id"] for x in spot(env, since=late)["items"]} == {b}
    assert {x["session_id"] for x in spot(env, until=env.client.get(f"{WS}/sessions/{a}").json()["started_at"])["items"]} == {a}
    newest = spot(env, sort="newest")["items"]
    assert newest[0]["session_id"] == b and newest[-1]["session_id"] == a
    assert spot(env, procedure="nope")["size"] == q["size"] == 10  # filters narrow the items, not the progress
    # unanswered items expire after 7 days; an answered one stays
    answer(env, q["items"][0])
    with Session(env.store.engine) as db:
        for sc in db.exec(select(SpotCheck)):
            sc.drawn_at = datetime.now(timezone.utc) - timedelta(days=8)
            db.add(sc)
        db.commit()
    q = spot(env)
    assert (q["size"], q["answered"], q["done"], len(q["items"])) == (1, 1, True, 1)


def test_a_batch_is_spread_across_procedures_and_agents_most_valuable_first(env):
    ids = [env.send("A") for _ in range(4)]
    with Session(env.store.engine) as db:  # two agents, and a close call and a changed decision to rank first
        for i, sid in enumerate(ids):
            db.get(SessionRecord, sid).agent_id = "harbor-lane" if i % 2 else "northwind"
        rows = list(db.exec(select(RuleResult).where(RuleResult.session_id == ids[0], RuleResult.kind == "step").order_by(RuleResult.index)))
        rows[0].decision_p, rows[0].decision_threshold = 0.5, 0.5
        rows[1].decision_p, rows[1].decision_threshold, rows[1].decided_status, rows[1].reviewed = 0.1, 0.5, "skipped", True
        db.commit()
    env.client.put(f"{WS}/spot-checks/settings", json={"batch": 4})
    q = spot(env)
    assert sorted(x["agent_id"] for x in q["items"]) == ["harbor-lane", "harbor-lane", "northwind", "northwind"]
    assert q["items"][0]["why"] == ["close_call"] and q["items"][0]["weight"] == 5.0
    # one from each agent per round, the most valuable first: northwind's close call and changed decision both make it
    assert {tuple(x["why"]) for x in q["items"] if x["agent_id"] == "northwind"} == {("close_call",), ("review_changed",)}
    assert spot(env, why="close_call")["items"][0]["session_id"] == ids[0]


def test_close_calls_weigh_more():
    def rr(p=None, t=None, decided=None, reviewed=None, status="done"):
        return RuleResult(decision_p=p, decision_threshold=t, decided_status=decided, reviewed=reviewed, status=status)

    plain, far, near, edge, changed = rr(), rr(0.99, 0.5), rr(0.55, 0.5), rr(0.5, 0.5), rr(0.2, 0.5, "skipped", True)
    assert weight(plain) == weight(far) == 1.0
    assert weight(edge) == 5.0 and 1.0 < weight(near) < weight(edge)
    assert weight(changed) == 5.0 and weight(rr(0.5 + CLOSE_BAND, 0.5)) == 1.0  # 0.3 from its threshold: only the changed decision counts
    assert weight(rr(0.2, 0.5, "done", True)) == 1.0  # reviewed, same decision


def test_decisions_are_recorded_and_changed_decisions_are_spot_checked_first(env, monkeypatch):
    """Decide mode: every decision says no (0.03), so the steps are decided skipped and reviewed (the reviewer says
    done), and the never rules (no action to ask about, in this fake's split) are decided ok and not reviewed."""
    monkeypatch.delenv("SOPOPS_CHECK_MODE")
    sid = env.send("CLEAN")
    with Session(env.store.engine) as db:
        rows = {(r.kind, r.index): r for r in db.exec(select(RuleResult).where(RuleResult.session_id == sid))}
    step, never = rows[("step", 1)], rows[("never", 2)]
    assert (step.status, step.decided_status, step.reviewed, step.decision_p) == ("done", "skipped", True, pytest.approx(0.03))
    assert (step.decision_threshold, never.status, never.decided_status, never.reviewed) == (0.5, "ok", "ok", False)
    items = spot(env)["items"]
    assert items[0]["why"] == ["review_changed"] and items[0]["decided_status"] == "skipped" and items[0]["kind"] == "step"
    weights = [x["weight"] for x in items]
    assert weights == sorted(weights, reverse=True) and weights[0] == 5.0


# --- calibration -------------------------------------------------------------------------------------------------


def reference_examples(store, ws, procedure_id):
    """`Verdicts.calibration_examples` as it was before rule labels and override statuses, verbatim but for the names."""

    def overrides(db, session_ids):
        ids = list(set(session_ids))
        if not ids:
            return {}
        out = {}
        for o in db.exec(select(FindingOverride).where(FindingOverride.session_id.in_(ids)).order_by(FindingOverride.created_at)):
            out[(o.session_id, o.procedure_id, o.rule_id)] = o
        return out

    def labels_of(db, findings):
        pids = {f.pattern_id for f in findings if f.pattern_id}
        pats = list(db.exec(select(Pattern).where(Pattern.id.in_(pids)))) if pids else []
        verdicts = effective(db, ws, pats)
        over = overrides(db, [f.session_id for f in findings])
        published = _published(db, ws, {f.rule_id for f in findings})
        out = {}
        for f in findings:
            v = verdicts.get(f.pattern_id or "")
            lab = v[0].verdict if v else None
            if v and lab in SUPPRESSING and (at := published.get((f.procedure_id, f.rule_id))) and at > _utc(v[0].created_at):
                lab = None
            out[f.id] = (lab, over.get((f.session_id, f.procedure_id, f.rule_id)))
        return out

    def reviewed_result(pc, rules, passed):
        if not passed or pc.checked_result in (None, "not_measurable"):
            return pc.checked_result
        judged = [Judged(r.rule_id, r.kind, r.index, PASS_STATUS.get(r.kind, r.status) if r.id in passed else r.status, [], "",
                         r.kind == "step" and r.status == "skipped" and LENIENT_MARK not in (r.reason or "")) for r in rules]
        return rollup(judged, pc.goal_reached).result

    with Session(store.engine, expire_on_commit=False) as db:
        findings = list(db.exec(select(Finding).where(Finding.workspace_id == ws, Finding.procedure_id == procedure_id)))
        labels = labels_of(db, findings)
        by_session = defaultdict(list)
        for f in findings:
            by_session[f.session_id].append(f)
        reviewed = sorted(sid for sid, fs in by_session.items() if any(Verdicts.wrong(*labels[f.id]) is not None for f in fs))
        if not reviewed:
            return []
        checks = {pc.session_id: pc for pc in db.exec(select(ProcedureCheck).where(ProcedureCheck.session_id.in_(reviewed), ProcedureCheck.procedure_id == procedure_id))}
        rules = defaultdict(list)
        for r in db.exec(select(RuleResult).where(RuleResult.session_id.in_(reviewed), RuleResult.procedure_id == procedure_id)):
            rules[r.session_id].append(r)
        sessions = {s.id: s for s in db.exec(select(SessionRecord).where(SessionRecord.id.in_(reviewed)))}
    out = []
    for sid in reviewed:
        pc = checks.get(sid)
        if pc is None:
            continue
        wrong = {f.rule_result_id for f in by_session[sid] if Verdicts.wrong(*labels[f.id])}
        failing = {f.rule_id for f in by_session[sid] if f.rule_result_id not in wrong}
        result = reviewed_result(pc, rules[sid], wrong)
        wrong_rules = {f.rule_id for f in by_session[sid] if f.rule_result_id in wrong}
        out.append(dspy.Example(turns=sessions[sid].turns, deviated=result == "deviated", failing=failing, wrong=wrong_rules, session_id=sid).with_inputs("turns"))
    return out


def as_rows(examples):
    return [(e.session_id, e.deviated, sorted(e.failing), sorted(e.wrong), e.turns) for e in examples]


def test_calibration_is_unchanged_without_new_labels(env):
    """Verdicts and overrides (no statuses, no rule labels): the examples, and so the fit, are what they were."""
    ids = [env.send("A"), env.send("A", also=True), env.send("A"), env.send("CLEAN"), env.send("A", also=True), env.send("A")]
    never = rule_pattern(env)
    assert as_rows(env.store.verdicts.calibration_examples("demo", ALLERGEN)) == as_rows(reference_examples(env.store, "demo", ALLERGEN)) == []
    verdict(env, never["pattern_id"], "real")
    env.client.post(f"{WS}/findings/{rule(env, ids[0], 'never', 1)['finding_id']}/override", json={"correct": False})
    env.client.post(f"{WS}/findings/{rule(env, ids[1], 'step', 2)['finding_id']}/override", json={"correct": False})
    step = rule_pattern(env, "step")
    verdict(env, step["pattern_id"], "checker_wrong")
    env.client.post(f"{WS}/findings/{rule(env, ids[4], 'step', 2)['finding_id']}/override", json={"correct": True})
    got, want = env.store.verdicts.calibration_examples("demo", ALLERGEN), reference_examples(env.store, "demo", ALLERGEN)
    assert len(want) == 5 and as_rows(got) == as_rows(want)
    # the calibrate report (a dry run) agrees with one from the reference examples
    report = env.client.post(f"{WS}/calibrate", json={"procedure": ALLERGEN, "dry_run": True}).json()["procedures"][0]
    assert (report["labels"], report["negative_labels"]) == (len(want), sum(len(e.wrong) for e in want))


def test_calibration_reads_corrected_statuses(env):
    a, b, c = env.send("A"), env.send("CLEAN"), env.send("A")
    # a missed mistake on a clean session: the session is labeled, the rule failing, the result deviated
    label(env, b, rule(env, b, "never", 2), "violated")
    # a spot check confirming a pass labels its session as the checker saw it
    label(env, c, rule(env, c, "step", 1), "done", source="spot_check")
    # a wrong finding with the status it should have had
    env.client.post(f"{WS}/findings/{rule(env, a, 'never', 1)['finding_id']}/override", json={"correct": False, "status": "ok"})
    ex = {e.session_id: e for e in env.store.verdicts.calibration_examples("demo", ALLERGEN)}
    never1, never2 = rule(env, a, "never", 1)["rule_id"], rule(env, b, "never", 2)["rule_id"]
    assert set(ex) == {a, b, c}
    assert (ex[a].deviated, ex[a].failing, ex[a].wrong) == (False, set(), {never1})
    assert (ex[b].deviated, ex[b].failing, ex[b].wrong) == (True, {never2}, set())
    assert (ex[c].deviated, ex[c].failing, ex[c].wrong) == (True, {never1}, set())
    # a correction to a step's status reaches the result too: not applicable instead of skipped
    d = env.send("CLEAN", also=True)
    step2 = rule(env, d, "step", 2)
    assert step2["finding"]
    label(env, d, step2, "not_applicable")
    e = next(x for x in env.store.verdicts.calibration_examples("demo", ALLERGEN) if x.session_id == d)
    assert (e.deviated, e.failing, e.wrong) == (False, set(), {step2["rule_id"]})


# --- model-call listeners ----------------------------------------------------------------------------------------


def test_model_call_listeners_see_each_call_and_never_break_a_check(env):
    seen = []

    def broken(call, result):
        raise RuntimeError("listener down")

    def broken_before(call):
        raise RuntimeError("listener down")

    assert not tracing.listening()
    a = tracing.add_listener(broken, broken_before)
    b = tracing.add_listener(lambda call, result: seen.append((call, result)), lambda call: {"x-trace": call.call_id})
    try:
        sid = env.send("A")
    finally:
        tracing.remove_listener(a)
        tracing.remove_listener(b)
    assert not tracing.listening()
    assert session(env, sid)["status"] == "checked"  # the broken listener didn't stop it
    review = [(c, r) for c, r in seen if c.signature == "CheckRules"]
    assert len(review) == 1
    call, result = review[0]
    assert (call.workspace, call.session_id, call.purpose, call.role, call.model, call.prompt_version, call.decision) == (
        "demo", sid, "review", "reasoning", "fake/reasoning", "check-5", False)
    assert call.procedure_id == ALLERGEN and set(call.context["rules"]) == set(call.inputs["rules_to_check"])
    assert call.context["rules"]["never_1"] == rule(env, sid, "never", 1)["rule_id"] and rule(env, sid, "never", 1)["rule_id"] in call.rule_ids
    assert "MODE:A" in call.inputs["transcript"] and result.outputs["goal_reached"] == "yes" and result.error is None
    assert next(j for j in result.outputs["rules"] if j["rule"] == "never_1")["status"] == "violated"
    assert result.input_tokens > 0 and result.output_tokens > 0 and result.latency_ms >= 0
    # decisions are traced and marked; every call of the session's check shares one run id
    decisions = [c for c, _ in seen if c.decision]
    assert decisions and all(c.role == "decide" and c.prompt_version == "decide-1" for c in decisions)
    runs = {c.check_run_id for c, _ in seen if c.session_id == sid and c.purpose in ("route", "check", "review")}
    assert len(runs) == 1 and None not in runs
    # headers go only to the hosted model gateway: the fakes' models aren't on it
    assert all(h == {} for h in env.fake.headers)
    # nothing is traced once no listener is left
    env.send("A")
    assert len([c for c, _ in seen if c.signature == "CheckRules"]) == 1


def test_a_listener_gets_errors_reasoning_and_gateway_headers():
    seen = []
    lst = tracing.add_listener(lambda call, result: seen.append((call, result)), lambda call: {"x-trace": "t1"})
    try:
        with tracing.context(procedure_id="p", rules={"step_1": "r1"}, check_run_id="run-1"):
            t = tracing.start("reasoning", dspy.Signature, {"x": 1}, "sopops/sopops-reasoning")
            assert t.gateway_headers("sopops/sopops-reasoning") == {"x-trace": "t1"} and t.gateway_headers("anthropic/claude-haiku-4-5") == {}
            t.done(dspy.Prediction(reasoning="because", answer=SimpleNamespace(value=True, probability=0.8)))
            tracing.start("decide", dspy.Signature, {}, "jev-1.13.0").failed(TimeoutError("slow"))
    finally:
        tracing.remove_listener(lst)
    (call, result), (dcall, dresult) = seen
    assert result.reasoning == "because" and result.outputs == {"answer": {"value": True, "probability": 0.8}}
    assert call.context == {"procedure_id": "p", "rules": {"step_1": "r1"}} and call.check_run_id == "run-1" and call.workspace is None
    assert dcall.decision and dresult.error == "TimeoutError: slow" and dresult.outputs == {}
    assert tracing.run_id("s", "rev") == tracing.run_id("s", "rev") != tracing.run_id("s", "rev2")


def test_models_send_listener_headers_to_the_gateway_only(monkeypatch):
    """`Models.predict` adds a listener's headers to the client copy for a `sopops/` model, and not for any other."""
    from sopops_server.ai.lm import Models

    got = {}

    def run(self, role, client, predictor, inputs, model=None):
        got[role] = client.kwargs.get("extra_headers")
        return dspy.Prediction(answer="a")

    monkeypatch.setattr(Models, "_run", run)
    monkeypatch.setenv("SOPOPS_LM_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("SOPOPS_LM_KEY", "test")
    lst = tracing.add_listener(lambda call, result: None, lambda call: {"x-trace": call.call_id})
    try:
        m = Models({"fast": "sopops/sopops-fast", "reasoning": "anthropic/claude-haiku-4-5"}, jev=False, system1_gateway=False, cache=False)
        m.predict("fast", dspy.Signature("question -> answer"), question="q")
        m.predict("reasoning", dspy.Signature("question -> answer"), question="q")
    finally:
        tracing.remove_listener(lst)
    assert set(got["fast"]) == {"x-trace"} and not got["reasoning"]


def test_labels_are_kept_by_session_and_rule(env):
    sid = env.send("A")
    label(env, sid, rule(env, sid, "step", 1), "skipped")
    with Session(env.store.engine) as db:
        (row,) = db.exec(select(RuleLabel)).all()
    assert (row.session_id, row.procedure_id, row.kind, row.checked_status, row.status, row.source) == (sid, ALLERGEN, "step", "done", "skipped", "correction")
    assert row.rule_result_id and WINDOW
