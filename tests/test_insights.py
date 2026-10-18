"""Insights metric definitions with a fake model: eligible sessions and denominators, whole-number percentages
rounded half up, deltas in points, the prior window, release comparison with minimum n, warning signs kept out of
pass rates, drill-down filters, and the report."""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from conftest import SOPC_BIN, WS, fresh_db_url
from fakes import pattern_answer, pattern_reply
from fastapi.testclient import TestClient

from sopops import Sopc, Store, create_app
from sopops.ai.fakes import FakeModels, FakeSystemOne
from sopops.ai.signatures import CheckRules, field_name
from sopops.insights import Filters, compare_flag, delta_points, pct, wilson
from sopops.jobs import Jobs

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
ALLERGEN = "allergen-check"


def model(role, signature, inputs):
    """The user's turn carries directives: DEV (never rule 1 violated), WARN (warning sign 1 present), EXCL (the
    session ended before the procedure finished)."""
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
    excluded = any("EXCL" in u["text"] for u in state["inputs"]["transcript"])
    mine = field_name(f"p_{ALLERGEN}")
    return {k: (0.03 if excluded and k.endswith("_responded") else 0.95) if k.startswith(mine) else 0.03 for k in questions}


@pytest.fixture(scope="module")
def env(tmp_path_factory, request):
    from conftest import FIXTURE, read_files

    if not SOPC_BIN:
        pytest.skip("needs sopc")
    tmp = tmp_path_factory.mktemp("insights")
    store = Store(fresh_db_url(tmp), Sopc(SOPC_BIN))
    os.environ["SOPOPS_CHECK_MODE"] = "review"  # the sessions are all processed here, during setup
    request.addfinalizer(lambda: os.environ.pop("SOPOPS_CHECK_MODE", None))
    client = TestClient(create_app(store, Jobs(store, use_hatchet=False, ai=FakeModels(model, FakeSystemOne(decide)))))
    files = read_files(FIXTURE / "sops")
    client.put(f"{WS}/files", json={"files": files})
    client.post(f"{WS}/releases", json={})
    # release 2: new guidance, same rules (rule ids come from sopc and stay the same)
    proc = files["procedures/allergen-check.yaml"].replace("Parents often ask", "Parents and carers often ask")
    client.put(f"{WS}/files", json={"files": {**files, "procedures/allergen-check.yaml": proc}})
    assert client.post(f"{WS}/releases", json={}).json()["release"]["number"] == 2
    token = client.post(f"{WS}/tokens").json()["token"]
    n = {"i": 0}

    def send(at, release, *flags):
        n["i"] += 1
        body = {
            "platform": "livekit", "external_id": f"s{n['i']}", "agent": "tonys-pizza", "release": release, "started_at": at.isoformat(),
            "turns": [{"role": "agent", "text": "Hi"}, {"role": "user", "text": f"{' '.join(flags)} peanut allergy, EpiPen"}, {"role": "agent", "text": "It is totally safe."}],
        }
        r = client.post(f"{WS}/sessions", json=body, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 201, r.text

    # current window [T0, T0 + 1 day]: release 1, 10 sessions (5 deviated, 1 with the warning sign); release 2, 4
    # followed sessions; 2 excluded. Prior window: 3 deviated sessions.
    for i in range(10):
        send(T0 + timedelta(minutes=i), 1, *(["DEV"] if i < 5 else []), *(["WARN"] if i == 9 else []))
    for i in range(4):
        send(T0 + timedelta(hours=2, minutes=i), 2)
    for i in range(2):
        send(T0 + timedelta(hours=3, minutes=i), 2, "EXCL")
    for i in range(3):
        send(T0 - timedelta(hours=12, minutes=i), 1, "DEV")
    params = {"start": T0.isoformat(), "end": (T0 + timedelta(days=1)).isoformat()}
    rule_ids = {(r["kind"], r["index"]): r["rule_id"] for r in client.get(f"{WS}/insights/procedures/{ALLERGEN}", params=params).json()["rules"]}
    return SimpleNamespace(store=store, client=client, params=params, rules=rule_ids)


def get(env, path, **extra):
    r = env.client.get(f"{WS}/insights/{path}", params={**env.params, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_percentages_intervals_and_flags():
    assert (pct(1, 8), pct(5, 8), pct(3, 8), pct(0, 5), pct(1, 0)) == (13, 63, 38, 0, None)  # half up; "—" when d = 0
    assert delta_points(64, 0) == 64 and delta_points(None, 3) is None
    assert wilson(0, 0) is None and wilson(10, 10)[1] == 100 and wilson(5, 10) == (24, 76)
    assert compare_flag(5, 9, 9, 9) == "thin"  # under the minimum n on a side: never flagged
    assert compare_flag(5, 50, 45, 50) == "improved" and compare_flag(45, 50, 5, 50) == "regressed"
    assert compare_flag(9, 10, 8, 10) == "steady"  # intervals overlap
    with pytest.raises(ValueError):
        Filters.make("w", start=T0, end=T0 + timedelta(days=400))
    f = Filters.make("w", start=T0, end=T0 + timedelta(days=1))
    assert f.prior().end < f.start and f.prior().end - f.prior().start == pytest.approx(f.end - f.start, abs=timedelta(milliseconds=1))


def test_overview_tiles_and_denominators(env):
    o = get(env, "overview")
    cur, prior = o["tiles"]["current"], o["tiles"]["prior"]
    assert (cur["sessions_checked"], cur["followed"], cur["need_attention"], cur["excluded"]) == (14, 9, 5, 2)
    assert (cur["followed_pct"], cur["need_attention_pct"], cur["pass_rate"], cur["measurable_checks"]) == (64, 36, 64, 14)
    assert (prior["sessions_checked"], prior["need_attention"], prior["followed_pct"]) == (3, 3, 0)
    assert o["tiles"]["delta_points"]["followed_pct"] == 64 and o["tiles"]["delta_pct"]["sessions_checked"] == 367  # (14 - 3) / 3
    assert o["trend"]["bucket"] == "day" and sum(b["n"] for b in o["trend"]["buckets"]) == 14
    assert [m["release"] for m in o["trend"]["releases"]] == [1, 2]
    [row] = o["scoreboard"]
    assert (row["procedure_id"], row["measurable"], row["deviated"], row["pass_rate"], row["excluded"], row["never_violations"]) == (ALLERGEN, 14, 5, 64, 2, 5)
    assert row["open_patterns"] == 1
    assert row["top_failing_rules"][0]["rule_id"] == env.rules[("never", 1)] and row["top_failing_rules"][0]["fail_rate"] == 36
    # filters narrow every number
    assert get(env, "overview", release=2)["tiles"]["current"]["sessions_checked"] == 4


def test_rule_ladder_keeps_warning_signs_out_of_pass_rates(env):
    p = get(env, f"procedures/{ALLERGEN}")
    rules = {(r["kind"], r["index"]): r for r in p["rules"]}
    never = rules[("never", 1)]
    assert (never["n"], never["passes"], never["fails"], never["pass_rate"], never["findings"]) == (14, 9, 5, 64, 5)
    assert never["wordings"] == 1
    w = rules[("warning", 1)]
    assert w["pass_rate"] is None and w["n"] == 14 and w["present"] == 1 and w["present_rate"] == 7
    [impact] = p["warning_impact"]
    assert (impact["present_n"], impact["present_pass_rate"], impact["absent_n"], impact["delta_points"]) == (1, 100, 13, None)  # thin: no delta
    split = p["release_split"]
    assert (split["a"], split["b"]) == (1, 2)
    row = next(r for r in split["rules"] if r["rule_id"] == never["rule_id"])
    assert (row["a_n"], row["a_pass_rate"], row["b_n"], row["b_pass_rate"], row["delta_points"], row["flag"]) == (10, 50, 4, 100, None, "thin")
    names = [pat["name"] for th in p["themes"] for pat in th["patterns"]]
    assert names == ["Never rule 1 broken"]


def test_release_compare_needs_min_n(env):
    c = get(env, "releases/compare")
    assert (c["a"], c["b"], c["min_n"]) == (1, 2, 10)
    never = next(r for r in c["rules"] if r["rule_id"] == env.rules[("never", 1)])
    assert never["rule_change"] is None and never["flag"] == "thin" and never["delta_points"] is None and never["raw_delta_points"] == 50
    assert never["a"]["interval"] == [24, 76] and never["b"]["thin"] is True
    [proc] = c["procedures"]
    assert (proc["a"]["n"], proc["a"]["pass_rate"], proc["b"]["n"], proc["b"]["pass_rate"], proc["flag"]) == (10, 50, 4, 100, "thin")


def test_rule_page_and_drill_down(env):
    rid = env.rules[("never", 1)]
    r = get(env, f"procedures/{ALLERGEN}/rules/{rid}")
    assert r["kind"] == "never" and r["overall"]["n"] == 14 and [x["release"] for x in r["releases"]] == [1, 2]
    assert len(r["recent_findings"]) == 5 and r["recent_findings"][0]["evidence"][0]["quote"] == "totally safe"
    assert len(r["wordings"]) == 1 and r["text"].startswith("Never say")
    s = lambda **q: len(env.client.get(f"{WS}/sessions", params={**q, "limit": 200}).json()["items"])  # noqa: E731
    assert s(procedure=ALLERGEN, procedure_result="deviated") == 8  # no window on the list unless since/until
    assert s(rule=rid, rule_status="violated") == 8 and s(rule=rid, rule_status=["ok"]) == 9
    assert s(finding=False) == 11 and s(release=2) == 6
    assert s(eligible=True, since=env.params["start"]) == 14
    pattern = r["patterns"][0]["pattern_id"]
    assert s(pattern=pattern) == 8


def test_report(env):
    rep = get(env, "report")
    h = rep["headline"]
    assert (h["sessions_checked"], h["followed"], h["need_attention"], h["excluded"], h["multi_problem_sessions"]) == (14, 9, 5, 2, 0)
    [proc] = rep["procedures"]
    [theme] = proc["themes"]
    [pat] = theme["patterns"]
    assert pat["rule"]["kind"] == "never" and pat["rule"]["index"] == 1 and pat["findings"] == 5 and pat["need_attention_pct"] == 100
    assert pat["examples"][0]["quote"] == "totally safe" and len(pat["session_ids"]) == 5
    assert theme["sessions"] == 5 and theme["need_attention_pct"] == 100 and pat["theme_name"] == theme["name"]


def test_bad_filters_are_422(env):
    assert env.client.get(f"{WS}/insights/overview", params={"window": "3w"}).status_code == 422


def test_tiles_problems_agents_and_spark(env):
    o = get(env, "overview")
    cur, prior = o["tiles"]["current"], o["tiles"]["prior"]
    assert (cur["never_violations"], cur["never_sessions"], prior["never_violations"]) == (5, 5, 3)
    assert cur["rule_n"] > cur["rule_passes"] and cur["rule_pass_rate"] == pct(cur["rule_passes"], cur["rule_n"])
    assert o["tiles"]["delta_pct"]["never_violations"] == 67 and "rule_pass_rate" in o["tiles"]["delta_points"]
    assert o["thresholds"]["min_n"] == 10 and o["thresholds"]["fail_bad"] == 15
    problems = {p["type"]: p for p in o["problems"]}
    assert (problems["never"]["checks"], problems["never"]["of"], problems["never"]["pct"], problems["skipped"]["checks"]) == (5, 14, 36, 0)
    [agent] = o["agents"]
    assert (agent["agent"], agent["measurable"], agent["followed"], agent["pass_rate"], agent["thin"]) == ("tonys-pizza", 14, 9, 64, False)
    assert agent["delta_points"] is None and agent["prior_measurable"] == 3  # the prior window is thin
    assert agent["top_gap"]["rule_id"] == env.rules[("never", 1)] and agent["top_gap"]["tone"] == "bad"
    [row] = o["scoreboard"]
    assert sum(p["n"] for p in row["spark"]) == 14 and [p["start"] for p in row["spark"]] == [b["start"] for b in o["trend"]["buckets"]]
    assert row["top_failing_rules"][0]["tone"] == "bad"
    assert all(b["end"] > b["start"] for b in o["trend"]["buckets"])
    # a rule judged fewer than min_n times is never "most failed"
    assert get(env, "overview", release=2)["scoreboard"][0]["top_failing_rules"] == []


def test_ladder_tones_and_most_failed(env):
    p = get(env, f"procedures/{ALLERGEN}")
    rules = {(r["kind"], r["index"]): r for r in p["rules"]}
    never = rules[("never", 1)]
    assert (never["tone"], never["most_failed"]) == ("bad", True)
    assert rules[("step", 1)]["reached_pct"] == 100 and rules[("step", 1)]["most_failed"] is False and never["reached_pct"] is None
    assert p["callouts"] == [] and {x["type"] for x in p["problems"]} >= {"never", "skipped"} and p["agents"][0]["measurable"] == 14


def test_callouts_hot_rule_and_rising_pattern(env):
    wide = {"start": (T0 - timedelta(hours=13)).isoformat(), "end": env.params["end"]}  # the prior window's 3 deviated sessions too
    o = get(env, "overview", **wide)
    kinds = {c["kind"]: c for c in o["callouts"]}
    hot = kinds["hot_rule"]
    assert hot["rule_id"] == env.rules[("never", 1)] and hot["title"].endswith("fails 47% of the time") and hot["drill"]["rule_status"] == ["violated"]
    assert kinds["rising_pattern"]["drill"]["pattern"] and "release" not in kinds  # release 2 is thin
    assert o["scoreboard"][0]["callouts"] == len(o["callouts"])


def test_compare_counts_and_timeline(env):
    c = get(env, "releases/compare")
    assert (c["sessions_a"], c["sessions_b"]) == (10, 4)
    [proc] = c["procedures"]
    assert (proc["a"]["followed"], proc["a"]["deviated"], proc["b"]["followed"]) == (5, 5, 4)
    assert [(t["release"], t["n"], t["pass_rate"], t["thin"]) for t in c["timeline"]] == [(1, 10, 50, False), (2, 4, 100, True)]
    never = next(r for r in c["rules"] if r["rule_id"] == env.rules[("never", 1)])
    assert never["a"]["counts"] == {"violated": 5, "ok": 5}


def test_sessions_rule_status_alone_and_procedure_dots(env):
    items = lambda **q: env.client.get(f"{WS}/sessions", params={**q, "limit": 200}).json()["items"]  # noqa: E731
    assert len(items(rule_status="violated")) == 8 and len(items(rule_status=["skipped"])) == 0
    focused = items(procedure=ALLERGEN, procedure_result="deviated")
    assert all(s["procedure_check"]["result"] == "deviated" for s in focused)
    dots = focused[0]["procedure_check"]["rules"]
    assert [d["kind"] for d in dots] == sorted((d["kind"] for d in dots), key=["step", "never", "warning"].index)
    assert any(d["kind"] == "never" and d["status"] == "violated" for d in dots)
    assert items()[0]["procedure_check"] is None

