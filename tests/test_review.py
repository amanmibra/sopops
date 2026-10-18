"""Review with a fake model: verdicts and their history, suppressed findings left out of Insights (a session whose only
deviation is suppressed counts as followed), overrides, calibration labels, verdicts carried over by a recluster, the
review queue, suggestions (a retry on a proposal that doesn't compile, apply as a draft, dismiss) and the regression
export."""

import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from conftest import SOPC_BIN, WS, fresh_db_url
from fakes import pattern_answer, pattern_reply
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from sopops import Sopc, Store, create_app
from sopops.ai.fakes import FakeModels, FakeSystemOne
from sopops.ai.signatures import CheckRules, ClusterFindings, SuggestEdit, field_name
from sopops.jobs import Jobs
from sopops.orm import Finding, Pattern, Verdict
from sopops.verdicts import carry_over

T0 = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
ALLERGEN = "allergen-check"
PATH = "procedures/allergen-check.yaml"
WINDOW = {"start": (T0 - timedelta(days=1)).isoformat(), "end": (T0 + timedelta(days=1)).isoformat()}
SAFE = 'Never say an item is "allergen-free" or "safe"'


class Model:
    """Every session goes to the allergen check. The user's first turn says `MODE:X`: the check breaks never rule 1
    (and skips step 2 with `ALSO`), with `mode X` in the reason. `SuggestEdit` answers with `edits` in turn (the last
    one again once they run out)."""

    def __init__(self):
        self.edits: list[str] = []
        self.suggest_inputs: list[dict] = []

    def __call__(self, role, signature, inputs):
        if signature is SuggestEdit:
            self.suggest_inputs.append(inputs)
            text = self.edits.pop(0) if len(self.edits) > 1 else self.edits[0]
            return {"block_id": inputs["procedure_id"], "new_text": text, "rationale": "Adds the exception."}
        if signature is not CheckRules:
            return pattern_reply(role, signature, inputs)
        mode = re.search(r"MODE:(\w+)", inputs["transcript"]).group(1)
        rules = []
        for key in inputs["rules_to_check"]:
            status = {"step": "done", "never": "ok", "warning": "absent"}[key.split("_")[0]]
            ev = []
            if key == "never_1":
                status, ev = "violated", [{"turn": 2, "quote": "totally safe"}]
            if key == "step_2" and "ALSO" in inputs["transcript"]:
                status = "skipped"
            rules.append({"rule": key, "reason": f"fake mode {mode}", "status": status, "evidence": ev})
        return {"rules": rules, "goal_reason": "g", "goal_reached": "yes"}

    def decide(self, state, questions):
        if "findings" in state["inputs"]:
            return pattern_answer(state, questions)
        allergen = field_name(f"p_{ALLERGEN}")
        return {k: 0.95 if k.startswith(allergen) else 0.03 for k in questions}


@pytest.fixture
def env(tmp_path, files, monkeypatch):
    if not SOPC_BIN:
        pytest.skip("needs sopc")
    monkeypatch.setenv("SOPOPS_CHECK_MODE", "review")
    store = Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN))
    model = Model()
    fake = FakeModels(model, FakeSystemOne(model.decide))
    client = TestClient(create_app(store, Jobs(store, use_hatchet=False, ai=fake)))
    client.put(f"{WS}/files", json={"files": files})
    client.post(f"{WS}/releases", json={})
    token = client.post(f"{WS}/tokens").json()["token"]
    n = {"i": 0}

    def send(mode, also=False):
        n["i"] += 1
        i = n["i"]
        body = {
            "platform": "livekit", "external_id": f"s{i}", "agent": "tonys-pizza", "started_at": (T0 + timedelta(minutes=i)).isoformat(),
            "turns": [{"role": "agent", "text": "Hi"}, {"role": "user", "text": f"MODE:{mode} {'ALSO' if also else ''} peanut allergy"}, {"role": "agent", "text": "It is totally safe."}],
        }
        r = client.post(f"{WS}/sessions", json=body, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    return SimpleNamespace(store=store, model=model, fake=fake, client=client, send=send, files=files)


def rule_pattern(env, rule_kind="never"):
    items = env.client.get(f"{WS}/patterns", params={"procedure": ALLERGEN}).json()["items"]
    return next(p for p in items if p["rule"]["kind"] == rule_kind)


def session(env, sid):
    return env.client.get(f"{WS}/sessions/{sid}").json()


def tiles(env):
    return env.client.get(f"{WS}/insights/overview", params=WINDOW).json()["tiles"]["current"]


def verdict(env, pattern_id, kind, **kw):
    r = env.client.post(f"{WS}/patterns/{pattern_id}/verdict", json={"verdict": kind, "suggest": False, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_acceptable_suppresses_findings_now_and_later_and_insights_leave_them_out(env):
    a, b = env.send("A"), env.send("A")
    c = env.send("A", also=True)  # never rule 1 broken and step 2 skipped (lenient: the goal was reached)
    assert [session(env, s)["result"] for s in (a, b, c)] == ["deviated"] * 3
    assert tiles(env)["need_attention"] == 3 and tiles(env)["suppressed"] == 0
    never = rule_pattern(env)

    out = verdict(env, never["pattern_id"], "acceptable", note="Saying an item has no peanuts is fine here.", reviewer="qa")
    assert out["verdict"] == "acceptable" and out["suppressed_count"] == 3 and out["inherited"] is False
    # a session whose only deviation is suppressed counts as followed; the checker's own result is kept
    a_detail = session(env, a)
    assert a_detail["result"] == "followed" and a_detail["checks"][0]["checked_result"] == "deviated"
    rule = next(r for r in a_detail["checks"][0]["rules"] if r["finding"])
    assert rule["suppressed"] is True and rule["finding_id"]
    assert session(env, c)["result"] == "partial"  # its skipped step still counts
    t = tiles(env)
    assert (t["need_attention"], t["followed"], t["partial"], t["never_violations"], t["never_sessions"], t["suppressed"]) == (0, 2, 1, 0, 0, 3)
    proc = env.client.get(f"{WS}/insights/procedures/{ALLERGEN}", params=WINDOW).json()
    ladder = next(r for r in proc["rules"] if r["kind"] == "never" and r["index"] == 1)
    assert (ladder["n"], ladder["findings"], ladder["suppressed"]) == (0, 0, 3)
    assert next(r for r in proc["rules"] if r["kind"] == "step" and r["index"] == 2)["fails"] == 1
    board = env.client.get(f"{WS}/insights/overview", params=WINDOW).json()["scoreboard"][0]
    assert board["suppressed"] == 3 and board["deviated"] == 0
    pats = env.client.get(f"{WS}/patterns", params={"procedure": ALLERGEN, **WINDOW}).json()["items"]
    assert next(p for p in pats if p["pattern_id"] == never["pattern_id"])["suppressed"] == 3
    # the sessions behind a number: drill-downs can leave suppressed findings out, as Insights does
    drill = {"rule": never["rule_id"], "rule_status": "violated"}
    assert [len(env.client.get(f"{WS}/sessions", params={**drill, **x}).json()["items"]) for x in ({}, {"suppressed": False}, {"suppressed": True})] == [3, 0, 3]
    assert len(env.client.get(f"{WS}/sessions", params={"pattern": never["pattern_id"], "suppressed": False}).json()["items"]) == 0

    # a later session in the pattern is suppressed as it comes in
    d = env.send("A")
    assert session(env, d)["result"] == "followed" and tiles(env)["suppressed"] == 4

    # the pattern page: the verdict, its history, and the suppressed findings it still shows
    detail = env.client.get(f"{WS}/patterns/{never['pattern_id']}", params=WINDOW).json()
    assert detail["verdict"]["note"].startswith("Saying") and detail["suppressed_count"] == 4 and detail["findings"] == 0 and len(detail["session_ids"]) == 4

    # a new verdict wins and the history is kept: real counts again
    verdict(env, never["pattern_id"], "real")
    detail = env.client.get(f"{WS}/patterns/{never['pattern_id']}", params=WINDOW).json()
    assert [v["verdict"] for v in detail["verdict_history"]] == ["real", "acceptable"] and detail["suppressed_count"] == 0
    assert session(env, a)["result"] == "deviated" and tiles(env)["need_attention"] == 4


def test_overrides_and_calibration_labels(env):
    ids = [env.send("A") for _ in range(3)]
    never = rule_pattern(env)
    finding = next(r for r in session(env, ids[0])["checks"][0]["rules"] if r["finding"])["finding_id"]
    r = env.client.post(f"{WS}/findings/{finding}/override", json={"correct": False, "note": "The agent said 'no peanuts'.", "reviewer": "qa"})
    assert r.status_code == 200 and r.json()["suppressed"] is True and r.json()["session_id"] == ids[0]
    assert session(env, ids[0])["result"] == "followed" and session(env, ids[0])["checks"][0]["rules"][3]["override"] is False
    assert env.client.post(f"{WS}/findings/nope/override", json={"correct": True}).status_code == 404

    # only reviewed findings are labels: one so far, a negative one
    ex = env.store.verdicts.calibration_examples("demo", ALLERGEN)
    assert [(e.session_id, e.deviated, e.failing) for e in ex] == [(ids[0], False, set())] and set(ex[0].inputs().keys()) == {"turns"}

    # a checker_wrong verdict makes every finding of the pattern a negative label, except one overridden as right
    verdict(env, never["pattern_id"], "checker_wrong")
    env.client.post(f"{WS}/findings/{next(r for r in session(env, ids[1])['checks'][0]['rules'] if r['finding'])['finding_id']}/override", json={"correct": True})
    labels = {e.session_id: (e.deviated, e.failing) for e in env.store.verdicts.calibration_examples("demo", ALLERGEN)}
    rule_id = never["rule_id"]
    assert labels == {ids[0]: (False, set()), ids[1]: (True, {rule_id}), ids[2]: (False, set())}
    assert [session(env, s)["result"] for s in ids] == ["followed", "deviated", "followed"]

    # an override outlives a re-check of the session (new findings, same session and rule)
    env.store.verdicts.refresh("demo", sessions=ids)
    with Session(env.store.engine) as db:
        assert {f.session_id: f.suppressed for f in db.exec(select(Finding))} == {ids[0]: True, ids[1]: False, ids[2]: True}


def test_review_queue(env):
    for _ in range(3):
        env.send("A", also=True)
    env.send("A")
    q = env.client.get(f"{WS}/review").json()
    assert q["total"] == 2 and [i["sessions"] for i in q["items"]] == [4, 3]  # most sessions first
    top = q["items"][0]
    assert top["name"] == "Never rule 1 broken" and top["procedure_name"] == "Allergen check" and SAFE in top["rule_text"]
    assert top["verdict"] is None and top["suggestion"] is None and top["new_findings"] == 4 and top["theme_name"] == "Never problems"
    assert len(top["examples"]) == 4 and top["examples"][0]["excerpt"] == "totally safe" and top["examples"][0]["finding_id"]

    verdict(env, top["pattern_id"], "real")
    q = env.client.get(f"{WS}/review").json()
    assert [i["name"] for i in q["items"]] == ["Step 2 missed"]
    env.send("A")  # a new finding since the verdict: back in the queue, with its verdict
    q = env.client.get(f"{WS}/review").json()
    back = next(i for i in q["items"] if i["pattern_id"] == top["pattern_id"])
    assert back["verdict"]["verdict"] == "real" and back["new_findings"] == 1
    # an acceptable pattern's new findings are suppressed: it stays out
    verdict(env, top["pattern_id"], "acceptable")
    env.send("A")
    assert top["pattern_id"] not in {i["pattern_id"] for i in env.client.get(f"{WS}/review").json()["items"]}


def test_verdicts_follow_reclustering(env):
    for mode in "AAAABBBB":
        env.send(mode)  # the 8th finding splits the rule into Mode A and Mode B
    with Session(env.store.engine) as db:
        pats = {p.name: p for p in db.exec(select(Pattern))}
    a, b, rule = pats["Mode A"], pats["Mode B"], pats["Never rule 1 broken"]
    verdict(env, a.id, "acceptable")
    # a sub-pattern without a verdict of its own has its rule pattern's
    verdict(env, rule.id, "real")
    assert env.client.get(f"{WS}/patterns/{b.id}").json()["verdict"]["inherited"] is True
    assert env.client.get(f"{WS}/patterns/{a.id}").json()["verdict"]["verdict"] == "acceptable"

    # a recluster that merges both modes into one cluster: one keeps its id, the other retires and its verdict
    # carries over to the merged pattern if that has none
    original = env.model.__call__

    def merging(role, signature, inputs):
        if signature is ClusterFindings:
            return {"modes": [{"name": "Mixed", "description": "Both.", "findings": [f.key for f in inputs["findings"]]}]}
        return original(role, signature, inputs)

    env.fake.responder = merging
    assert env.store.patterns.recluster("demo", ALLERGEN, a.rule_id, env.fake, force=True)
    with Session(env.store.engine) as db:
        after = {p.id: p for p in db.exec(select(Pattern).where(Pattern.kind == "sub"))}
        own = {v.pattern_id: v for v in db.exec(select(Verdict).order_by(Verdict.created_at))}
    survivor = next(p for p in after.values() if p.status == "active")
    assert own[survivor.id].verdict == "acceptable"
    with Session(env.store.engine) as db:
        assert all(f.suppressed for f in db.exec(select(Finding).where(Finding.pattern_id == survivor.id)))

    # carry_over never replaces a successor's own verdict
    with Session(env.store.engine) as db, db.begin():
        db.add(Verdict(id="v-new", workspace_id="demo", pattern_id="p-new", verdict="real", created_at=T0))
        db.add(Verdict(id="v-old", workspace_id="demo", pattern_id="p-old", verdict="acceptable", created_at=T0))
    with Session(env.store.engine) as db, db.begin():
        carry_over(db, "demo", [("p-old", "p-new"), ("p-old", "p-free")], T0)
    with Session(env.store.engine) as db:
        got = {v.pattern_id: (v.verdict, v.carried_from) for v in db.exec(select(Verdict).where(Verdict.pattern_id.in_(["p-new", "p-free"])))}
    assert got == {"p-new": ("real", None), "p-free": ("acceptable", "p-old")}


def test_suggestions_retry_apply_and_dismiss(env):
    for _ in range(3):
        env.send("A")
    never = rule_pattern(env)
    old = env.files[PATH]
    fixed = old.replace(f"  - {SAFE}\n", f"  - {SAFE}, unless you have just checked the item with lookup_allergens\n")
    assert fixed != old
    env.model.edits = ["procedureSteps: [\n", fixed]  # the first proposal doesn't compile: sent back once with the error

    r = env.client.post(f"{WS}/patterns/{never['pattern_id']}/verdict", json={"verdict": "acceptable", "note": "Fine after a lookup."})
    assert r.status_code == 200 and r.json()["suggestion"]["status"] == "generating"
    sid = r.json()["suggestion"]["suggestion_id"]
    s = env.client.get(f"{WS}/suggestions/{sid}").json()
    assert s["status"] == "proposed" and s["attempts"] == 2 and s["old_text"] == old and s["new_text"] == fixed and s["block_id"] == ALLERGEN
    assert s["verdict"]["verdict"] == "acceptable" and s["pattern"]["pattern_id"] == never["pattern_id"] and s["path"] == PATH
    agents = {a["agent"]: a for a in s["plan"]["agents"]}
    assert set(agents) == {"luigis-trattoria", "sakura-sushi", "tonys-pizza"} and agents["tonys-pizza"]["added"] == 1 and "lookup_allergens" in agents["tonys-pizza"]["diff"]
    first, second = env.model.suggest_inputs
    assert first["previous_error"] == "" and second["previous_error"] and second["previous_text"] == "procedureSteps: [\n"
    assert first["verdict"] == "acceptable" and first["note"] == "Fine after a lookup." and first["rule"].startswith("Never 1: ") and len(first["examples"]) == 3
    assert [x["suggestion_id"] for x in env.client.get(f"{WS}/suggestions", params={"status": "proposed"}).json()["items"]] == [sid]
    assert env.client.get(f"{WS}/review").json()["items"] == []  # acceptable: nothing new to review

    # apply, with no repo connection: a draft edit, in Unpublished changes
    r = env.client.post(f"{WS}/suggestions/{sid}/apply", json={"reviewer": "qa"})
    assert r.status_code == 200 and r.json()["status"] == "applied"
    draft = env.client.get(f"{WS}/draft").json()
    assert {(i["kind"], i["id"], i["change"]) for i in draft["items"]} == {("procedure", ALLERGEN, "edited")}
    assert env.client.post(f"{WS}/suggestions/{sid}/apply").status_code == 409  # only once
    assert env.client.get(f"{WS}/patterns/{never['pattern_id']}").json()["suggestions"][0]["status"] == "applied"

    # a suggestion made before the file changed can't be applied; it can be dismissed
    env.model.edits = [fixed.replace("lookup_allergens", "the allergen lookup")]
    env.store.edit("demo", {PATH: old})
    later = env.client.post(f"{WS}/patterns/{never['pattern_id']}/suggest").json()
    assert later["status"] == "generating"  # generated by a job (here, right after the response)
    assert env.client.get(f"{WS}/suggestions/{later['suggestion_id']}").json()["status"] == "proposed"
    env.store.edit("demo", {PATH: fixed})
    assert env.client.post(f"{WS}/suggestions/{later['suggestion_id']}/apply").status_code == 409
    d = env.client.post(f"{WS}/suggestions/{later['suggestion_id']}/dismiss", json={"note": "Superseded."}).json()
    assert d["status"] == "dismissed" and d["note"] == "Superseded."


def test_suggestions_fail_and_need_a_verdict_that_calls_for_one(env):
    for _ in range(2):
        env.send("A")
    never = rule_pattern(env)
    assert env.client.post(f"{WS}/patterns/{never['pattern_id']}/suggest").status_code == 409  # no verdict yet
    verdict(env, never["pattern_id"], "checker_wrong")
    assert env.client.post(f"{WS}/patterns/{never['pattern_id']}/suggest").status_code == 409
    verdict(env, never["pattern_id"], "sop_wrong")
    env.model.edits = ["procedureSteps: [\n"]
    s = env.client.post(f"{WS}/patterns/{never['pattern_id']}/suggest").json()
    s = env.client.get(f"{WS}/suggestions/{s['suggestion_id']}").json()
    assert s["status"] == "failed" and s["error"] and s["attempts"] == 2 and s["plan"] is None
    assert env.client.post(f"{WS}/suggestions/{s['suggestion_id']}/apply").status_code == 409


def test_regression_export(env):
    a = env.send("A")
    b = env.send("A")
    never = rule_pattern(env)
    assert env.client.get(f"{WS}/patterns/{never['pattern_id']}/regression").status_code == 409  # needs a verdict
    verdict(env, never["pattern_id"], "real")
    finding = next(r for r in session(env, b)["checks"][0]["rules"] if r["finding"])["finding_id"]
    env.client.post(f"{WS}/findings/{finding}/override", json={"correct": False})
    r = env.client.get(f"{WS}/patterns/{never['pattern_id']}/regression")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(x) for x in r.text.splitlines()]
    assert [(x["session_id"], x["verdict"], x["expect"], x["observed_status"], x["expected_status"]) for x in lines] == [
        (a, "real", "agent", "violated", "ok"), (b, "checker_wrong", "checker", "violated", "ok")]
    assert lines[0]["transcript_ref"] == f"{WS}/sessions/{a}" and lines[0]["rule_text"] == SAFE and lines[0]["rule_kind"] == "never"
