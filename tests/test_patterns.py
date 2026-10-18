"""Patterns with a fake model: rule patterns, the first split into failure modes, assignment of new findings, stable
ids and names across reclustering, retiring, the triggers, and themes."""

import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from conftest import SOPC_BIN, WS, fresh_db_url
from fakes import pattern_answer, pattern_reply
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from sopops import Sopc, Store, create_app
from sopops.ai.fakes import FakeModels, FakeSystemOne
from sopops.ai.lm import set_ai
from sopops.ai.programs import rule_phrase
from sopops.ai.signatures import CheckRules, ClusterFindings, GroupThemes, NameRulePattern, field_name
from sopops.cli import main as cli
from sopops.jobs import Jobs
from sopops.orm import Finding, Pattern, Theme
from sopops.patterns import OTHER_MIN_NEW, RECLUSTER_NEW, SPLIT_AT, jaccard, reason_due

NEVER_1 = 'Never say an item is "allergen-free" or "safe"'  # the rule pattern's name: the rule's text, without a model

T0 = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
ALLERGEN = "allergen-check"


class Model:
    """Routes every session to the allergen check. The user's first turn says `MODE:X`; the check then breaks never
    rule 1 (and skips step 2 when it says `ALSO`), with `mode X` in the reason. Pattern calls go to `fakes`.
    `rename` makes later clusterings name their modes differently, to show old names are kept."""

    def __init__(self):
        self.rename = ""
        self.cluster_calls = 0

    def __call__(self, role, signature, inputs):
        if signature is not CheckRules:
            reply = pattern_reply(role, signature, inputs)
            if signature is ClusterFindings:
                self.cluster_calls += 1
                for m in reply["modes"]:
                    m["name"] += self.rename
            return reply
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
            "platform": "livekit", "external_id": f"s{i}", "agent": "tonys-pizza",
            "started_at": (T0 + timedelta(minutes=i)).isoformat(), "ended_at": (T0 + timedelta(minutes=i, seconds=50)).isoformat(),
            "turns": [{"role": "agent", "text": "Hi"}, {"role": "user", "text": f"MODE:{mode} {'ALSO' if also else ''} peanut allergy"}, {"role": "agent", "text": "It is totally safe."}],
        }
        r = client.post(f"{WS}/sessions", json=body, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    return SimpleNamespace(store=store, model=model, fake=fake, client=client, send=send)


def patterns(store):
    with Session(store.engine) as db:
        return list(db.exec(select(Pattern)))


def finding_patterns(store):
    with Session(store.engine) as db:
        return {f.session_id: f.pattern_id for f in db.exec(select(Finding).where(Finding.status == "violated"))}


def by_name(store):
    return {p.name: p for p in patterns(store)}


def test_findings_join_their_rule_pattern_until_the_rule_is_split(env):
    ids = [env.send("A") for _ in range(SPLIT_AT - 1)]
    rule = [p for p in patterns(env.store) if p.kind == "rule"]
    assert [(p.name, p.finding_count, p.session_count) for p in rule] == [(NEVER_1, SPLIT_AT - 1, SPLIT_AT - 1)]
    assert set(finding_patterns(env.store).values()) == {rule[0].id}
    assert env.model.cluster_calls == 0
    assert rule[0].first_seen is not None and rule[0].last_seen > rule[0].first_seen
    # themes: the new rule pattern got one (fake: one theme per rule kind)
    with Session(env.store.engine) as db:
        themes = list(db.exec(select(Theme)))
    assert [t.name for t in themes] == ["Never problems"] and rule[0].theme_id == themes[0].id
    assert len(ids) == SPLIT_AT - 1


def test_split_assign_and_stable_ids_across_reclustering(env):
    for mode in "AAAABBB":
        env.send(mode)
    env.send("B")  # the 8th finding on the rule: split
    assert env.model.cluster_calls == 1
    pats = by_name(env.store)
    a, b, other, rule = pats["Mode A"], pats["Mode B"], pats["Other"], pats[NEVER_1]
    assert (a.kind, b.kind, other.kind) == ("sub", "sub", "other")
    assert a.parent_id == b.parent_id == other.parent_id == rule.id
    assert (a.finding_count, b.finding_count, other.finding_count, rule.finding_count) == (4, 4, 0, 8)
    assert rule.clustered_count == 8 and a.theme_id == rule.theme_id is not None

    # new findings: the fast model picks the mode, or other
    sa, sc = env.send("A"), env.send("C")
    fp = finding_patterns(env.store)
    assert fp[sa] == a.id and fp[sc] == other.id
    assert env.model.cluster_calls == 1  # nothing due yet

    # recluster with differently named modes and a new mode C: ids and names of A and B stay, C is new
    env.send("C")
    env.model.rename = " (renamed)"
    assert env.store.patterns.recluster("demo", ALLERGEN, a.rule_id, env.fake, force=True)
    after = {p.id: p for p in patterns(env.store)}
    assert after[a.id].name == "Mode A" and after[b.id].name == "Mode B" and after[a.id].status == "active"
    new = [p for p in after.values() if p.name == "Mode C (renamed)"]
    assert len(new) == 1 and new[0].finding_count == 2 and new[0].kind == "sub"
    assert after[other.id].finding_count == 0 and after[a.id].finding_count == 5

    # a mode that disappears is retired (its verdicts stay attached), and its findings move
    env.model.rename = ""
    original = env.model.__call__

    def without_b(role, signature, inputs):
        reply = original(role, signature, inputs)
        if signature is ClusterFindings:
            reply["modes"] = [m for m in reply["modes"] if not m["name"].startswith("Mode B")]
        return reply

    env.fake.responder = without_b
    assert env.store.patterns.recluster("demo", ALLERGEN, a.rule_id, env.fake, force=True)
    after = {p.id: p for p in patterns(env.store)}
    assert after[b.id].status == "retired" and after[b.id].finding_count == 0
    assert after[other.id].finding_count == 4 and after[a.id].name == "Mode A"


def test_triggers():
    rule = Pattern(id="r", workspace_id="w", procedure_id="p", rule_id="x", kind="rule", name="n", created_at=T0, updated_at=T0)
    sub = Pattern(id="s", workspace_id="w", procedure_id="p", rule_id="x", kind="sub", name="s", created_at=T0, updated_at=T0)
    other = Pattern(id="o", workspace_id="w", procedure_id="p", rule_id="x", kind="other", name="Other", created_at=T0, updated_at=T0)
    rule.finding_count = SPLIT_AT - 1
    assert reason_due(rule, []) is None
    rule.finding_count = SPLIT_AT
    assert reason_due(rule, []) == "split"
    # split already: new findings, or too many in other
    rule.clustered_count = 40
    rule.finding_count = 40 + RECLUSTER_NEW - 1
    other.finding_count = 2
    assert reason_due(rule, [sub, other]) is None
    rule.finding_count = 40 + RECLUSTER_NEW
    assert reason_due(rule, [sub, other]) == "new_findings"
    rule.finding_count, other.finding_count = 40 + OTHER_MIN_NEW, 12  # 12 / 45 > 25%
    assert reason_due(rule, [sub, other]) == "other_share"
    rule.finding_count = 40 + OTHER_MIN_NEW - 1
    assert reason_due(rule, [sub, other]) is None
    # clustered once but no mode found: wait for SPLIT_AT more
    rule.clustered_count, rule.finding_count = 9, 9 + SPLIT_AT - 1
    assert reason_due(rule, [other]) is None
    rule.finding_count = 9 + SPLIT_AT
    assert reason_due(rule, [other]) == "split"
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_themes_keep_their_names_and_ids(env):
    env.send("A", also=True)  # a never finding and a step finding
    with Session(env.store.engine) as db:
        themes = {t.name: t for t in db.exec(select(Theme))}
    assert set(themes) == {"Step problems", "Never problems"}
    assert [t.position for t in sorted(themes.values(), key=lambda t: t.name)] == [1, 0]
    # regrouping with a model that renames: existing themes keep their names and ids
    original = env.model.__call__

    def renaming(role, signature, inputs):
        reply = original(role, signature, inputs)
        if signature is GroupThemes:
            for t in reply["themes"]:
                t["name"] = "Something else " + t["name"]
        return reply

    env.fake.responder = renaming
    assert env.store.patterns.regroup_themes("demo", ALLERGEN, env.fake, force=True)
    with Session(env.store.engine) as db:
        again = {t.name: t.id for t in db.exec(select(Theme))}
    assert again == {k: v.id for k, v in themes.items()}

    # a model that swaps the existing keys between groups: matched by their patterns instead
    def swapping(role, signature, inputs):
        reply = original(role, signature, inputs)
        if signature is GroupThemes:
            keys = [t["key"] for t in reply["themes"]]
            for t, k in zip(reply["themes"], reversed(keys)):
                t["key"] = k
        return reply

    env.fake.responder = swapping
    assert env.store.patterns.regroup_themes("demo", ALLERGEN, env.fake, force=True)
    with Session(env.store.engine) as db:
        again = {t.name: t.id for t in db.exec(select(Theme))}
        step_theme = {p.rule_id: p.theme_id for p in db.exec(select(Pattern).where(Pattern.kind == "rule"))}
    assert again == {k: v.id for k, v in themes.items()}
    assert sorted(step_theme.values()) == sorted(t.id for t in themes.values())


def test_pattern_api_and_recluster_endpoint(env):
    for mode in "AAAABBBB":
        env.send(mode)
    r = env.client.get(f"{WS}/patterns", params={"procedure": ALLERGEN})
    assert r.status_code == 200
    rules = r.json()["items"]
    assert len(rules) == 1 and {s["name"] for s in rules[0]["sub_patterns"]} == {"Mode A", "Mode B", "Other"}
    pid = next(s["pattern_id"] for s in rules[0]["sub_patterns"] if s["name"] == "Mode A")
    one = env.client.get(f"{WS}/patterns/{pid}").json()
    assert one["name"] == "Mode A" and one["findings"] == 4 and len(one["session_ids"]) == 4
    # the sessions list filters by pattern; a rule pattern includes its sub-patterns
    assert len(env.client.get(f"{WS}/sessions", params={"pattern": pid}).json()["items"]) == 4
    assert len(env.client.get(f"{WS}/sessions", params={"pattern": rules[0]["pattern_id"]}).json()["items"]) == 8
    r = env.client.post(f"{WS}/patterns/recluster", json={"procedure": ALLERGEN, "force": True})
    assert r.status_code == 200 and r.json()["reclustered"] == [rules[0]["rule_id"]]


def test_recluster_cli(env, capsys):
    from sopops.cli import main as cli

    url = env.store.engine.url.render_as_string(hide_password=False)
    for mode in "AAAABBB":
        env.send(mode)
    set_ai(env.fake)
    cli(["recluster", "--database-url", url, "--workspace", "demo", "--force"])
    assert "0 rules reclustered" in capsys.readouterr().out  # 7 findings: under the minimum to split, even with --force
    env.send("B")  # the pipeline splits the rule itself at 8
    assert env.model.cluster_calls == 1
    cli(["recluster", "--database-url", url, "--workspace", "demo"])
    assert "0 rules reclustered" in capsys.readouterr().out  # nothing due
    cli(["recluster", "--database-url", url, "--workspace", "demo", "--force"])
    assert "1 rules reclustered" in capsys.readouterr().out and env.model.cluster_calls == 2
    assert {"Mode A", "Mode B"} <= set(by_name(env.store))


def test_rule_phrase_trims_a_rule_to_a_short_name():
    assert rule_phrase("Never tell the caller a delivery time") == "Never tell the caller a delivery time"
    assert rule_phrase("Read the whole order back (items, sizes) before confirming") == "Read the whole order back"
    assert rule_phrase("Check each item against {{menu_allergen_link}}; never guess.") == "Check each item against menu_allergen_link"
    assert rule_phrase("Never volunteer prices or descriptions of menu items unless the caller asks") == "Never volunteer prices or descriptions of menu items…"
    assert rule_phrase("") == "Rule"


def test_a_rule_pattern_is_named_once_by_the_reasoning_model(env, monkeypatch):
    model_call = env.model.__call__
    asked = []

    def named(role, signature, inputs):
        if signature is NameRulePattern:
            asked.append(inputs)
            return {"name": "Called a dish safe."}
        return model_call(role, signature, inputs)

    monkeypatch.setattr(env.fake, "responder", named)
    env.send("A")
    env.send("A")
    (rule,) = [p for p in patterns(env.store) if p.kind == "rule"]
    assert rule.name == "Called a dish safe"
    assert [(a["kind"], a["rule"], a["procedure"]) for a in asked] == [("never", NEVER_1, "Allergen check")]  # once, when it was made
    got = env.client.get(f"{WS}/patterns", params={"window": "365d"}).json()["items"][0]
    assert (got["name"], got["description"], got["rule"]["text"]) == ("Called a dish safe", NEVER_1, NEVER_1)

    asked.clear()
    monkeypatch.setattr(env.fake, "responder", lambda role, signature, inputs: {"name": "Step 1"} if signature is NameRulePattern else model_call(role, signature, inputs))
    assert env.store.patterns.name("demo", force=True, ai=env.fake) == 1  # a rule number won't do: the rule's text
    (rule,) = [p for p in patterns(env.store) if p.kind == "rule"]
    assert rule.name == NEVER_1


def test_index_names_are_renamed_by_the_migration_then_the_backfill(env, monkeypatch, capsys):
    env.send("A")
    with Session(env.store.engine) as db, db.begin():  # as a database from before 0015 has it
        db.exec(text("UPDATE patterns SET name = 'Never rule 1 broken' WHERE kind = 'rule'"))
        db.exec(text("UPDATE alembic_version SET version_num = '0014'"))
    url = env.store.engine.url.render_as_string(hide_password=False)
    again = Store(url, Sopc(SOPC_BIN))
    (rule,) = [p for p in patterns(again) if p.kind == "rule"]
    assert rule.name == NEVER_1  # from the rule's text, no model

    set_ai(FakeModels(lambda role, signature, inputs: {"name": "Called a dish safe"}, models={"reasoning": "fake/reasoning"}))
    cli(["patterns", "rename", "--database-url", url, "--workspace", "demo"])
    assert "demo: 1 rule patterns renamed" in capsys.readouterr().out
    (rule,) = [p for p in patterns(again) if p.kind == "rule"]
    assert rule.name == "Called a dish safe"
    cli(["patterns", "rename", "--database-url", url, "--workspace", "demo"])
    assert "demo: 0 rule patterns renamed" in capsys.readouterr().out  # a model's name is kept

