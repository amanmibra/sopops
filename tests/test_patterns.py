"""Patterns with a fake model: rule patterns, the first split into failure modes, assignment of new findings, stable
ids and names across reclustering, retiring, the triggers, and themes."""

import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from conftest import SOPC_BIN, WS, fresh_db_url
from fakes import PATTERN_SCHEMAS, pattern_reply
from sopops import Sopc, Store, create_app
from sopops.jobs import Jobs
from sopops.llm import FakeLLM, text_of
from sopops.models import Finding, Pattern, Theme
from sopops.patterns import OTHER_MIN_NEW, RECLUSTER_NEW, SPLIT_AT, jaccard, reason_due

T0 = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
ALLERGEN = "allergen-check"


class Model:
    """Routes every session to the allergen check. The user's first turn says `MODE:X`; the check then breaks never
    rule 1 (and skips step 2 when it says `ALSO`), with `mode X` in the reason. Pattern calls go to `fakes`.
    `rename` makes later clusterings name their modes differently, to show old names are kept."""

    def __init__(self):
        self.rename = ""
        self.cluster_calls = 0

    def __call__(self, role, messages, schema):
        text = text_of(messages)
        if schema.__name__ in PATTERN_SCHEMAS:
            reply = pattern_reply(role, messages, schema)
            if schema.__name__ == "ClusterReply":
                self.cluster_calls += 1
                for m in reply["modes"]:
                    m["name"] += self.rename
            return reply
        if role == "fast":
            ids = re.findall(r'<procedure id="([^"]+)">', text)
            return {"procedures": [{"procedure": p, "reason": "r", "request": "yes" if p == ALLERGEN else "no", "agent_responded": "yes" if p == ALLERGEN else "no"} for p in ids]}
        mode = re.search(r"MODE:(\w+)", text).group(1)
        rules = []
        for key in re.findall(r"^- ((?:step|never|warning)_\d+): ", text, re.M):
            status = {"step": "done", "never": "ok", "warning": "absent"}[key.split("_")[0]]
            ev = []
            if key == "never_1":
                status, ev = "violated", [{"turn": 2, "quote": "totally safe"}]
            if key == "step_2" and "ALSO" in text:
                status = "skipped"
            rules.append({"rule": key, "reason": f"fake mode {mode}", "status": status, "evidence": ev})
        return {"rules": rules, "goal_reason": "g", "goal_reached": "yes"}


@pytest.fixture
def env(tmp_path, files):
    store = Store(fresh_db_url(tmp_path), Sopc(SOPC_BIN))
    model = Model()
    fake = FakeLLM(model)
    client = TestClient(create_app(store, Jobs(store, use_hatchet=False, llm=fake)))
    client.put(f"{WS}/files", json={"files": files})
    client.post(f"{WS}/releases", json={})
    token = client.post(f"{WS}/ingest-token").json()["token"]
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
    assert [(p.name, p.finding_count, p.session_count) for p in rule] == [("Never rule 1 broken", SPLIT_AT - 1, SPLIT_AT - 1)]
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
    a, b, other, rule = pats["Mode A"], pats["Mode B"], pats["Other"], pats["Never rule 1 broken"]
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

    def without_b(role, messages, schema):
        reply = original(role, messages, schema)
        if schema.__name__ == "ClusterReply":
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

    def renaming(role, messages, schema):
        reply = original(role, messages, schema)
        if schema.__name__ == "ThemesReply":
            for t in reply["themes"]:
                t["name"] = "Something else " + t["name"]
        return reply

    env.fake.responder = renaming
    assert env.store.patterns.regroup_themes("demo", ALLERGEN, env.fake, force=True)
    with Session(env.store.engine) as db:
        again = {t.name: t.id for t in db.exec(select(Theme))}
    assert again == {k: v.id for k, v in themes.items()}

    # a model that swaps the existing keys between groups: matched by their patterns instead
    def swapping(role, messages, schema):
        reply = original(role, messages, schema)
        if schema.__name__ == "ThemesReply":
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
    from sopops import llm
    from sopops.cli import main as cli

    url = env.store.engine.url.render_as_string(hide_password=False)
    for mode in "AAAABBB":
        env.send(mode)
    llm.set_llm(env.fake)
    cli(["recluster", "--database-url", url, "--workspace", "demo", "--force"])
    assert "0 rules reclustered" in capsys.readouterr().out  # 7 findings: under the minimum to split, even with --force
    env.send("B")  # the pipeline splits the rule itself at 8
    assert env.model.cluster_calls == 1
    cli(["recluster", "--database-url", url, "--workspace", "demo"])
    assert "0 rules reclustered" in capsys.readouterr().out  # nothing due
    cli(["recluster", "--database-url", url, "--workspace", "demo", "--force"])
    assert "1 rules reclustered" in capsys.readouterr().out and env.model.cluster_calls == 2
    assert {"Mode A", "Mode B"} <= set(by_name(env.store))
