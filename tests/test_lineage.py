"""Rule lineage: the matcher (its thresholds against the fixture and walkthrough procedures), links written at publish
(an applied suggestion, a hand rewording, a moved and reworded rule), the full loop (a verdict, its suggestion applied
and published, then sessions on the reworded rule landing in the same pattern and moving it to fixed or regressed),
Insights, sessions and calibration across wordings, linking and unlinking by hand, rebuilding, and the migration's
backfill."""

import itertools

import pytest
from conftest import SOPC_BIN, WS, downgrade, fresh_db_url
from sqlalchemy import text
from sqlmodel import Session, select
from test_review import ALLERGEN, PATH, SAFE, Model, env, queue, state_of, verdict  # noqa: F401  (env is a fixture)

from sopops_server import Sopc, Store
from sopops_server.ai import programs
from sopops_server.cli import main as cli
from sopops_server.lineage import ANYWHERE_MIN, SAME_POSITION_MIN, match, similarity
from sopops_server.orm import Finding, Pattern, RuleLineage, Verdict
from sopops_server.patterns import rekey

# Every procedure of the test fixture and the walkthrough, by kind: two different rules of one procedure must never
# match at their position.
PROCEDURES = {
    "fixture allergen-check": {"step": ["Ask if anyone in the order has a food allergy", "Name the specific allergen back to the customer",
                                        "Check each item the customer ordered against {{menu_allergen_link}}"],
                               "never": ['Never say an item is "allergen-free" or "safe"', "Never place the order before allergens are confirmed"]},
    "fixture delivery-handling": {"step": ["Ask for the full delivery address", "Check the address is inside the delivery zone", "Tell the customer the estimated delivery time"]},
    "fixture large-orders": {"step": ["Confirm the full item list back to the customer", "Check kitchen capacity for the requested time",
                                      "Offer the earliest time the kitchen can meet"]},
    "fixture reservations": {"step": ["Ask for party size, date and time", "Check availability", "Confirm the booking details and the name on the reservation"]},
    "walkthrough allergen-check": {"step": ["Ask who the allergy is for and what it is", "Name the specific allergen back to the caller",
                                            "Check each item the caller ordered against {{menu_allergen_link}}"],
                                   "never": ['Never say an item is "allergen-free" or "safe"', "Never place the order before allergens are confirmed",
                                             "Never tell a caller that a dish is safe for their allergy"]},
    "walkthrough delivery-orders": {"step": ["Ask for the full delivery address", "Check the address is inside the delivery zone", "Read back the order and the total"]},
    "walkthrough reservations": {"never": ["Never confirm a booking for more than 8 guests; {{staff_transfer}} confirms those", "Never double-book a table"]},
}
# Rewordings: the walkthrough's and the tests' suggested edits, and small hand edits.
REWORDED = [
    ("Never tell the caller a delivery time", "Never tell the caller a delivery time before the delivery zone check confirms the address"),
    (SAFE, f"{SAFE}; say what you checked instead"),
    (SAFE, f"{SAFE}, unless you have just checked the item with lookup_allergens"),
    ("Confirm the full item list back to the customer", "Read the full order back to the customer"),
    ("Ask if anyone in the order has a food allergy", "Ask whether anyone in the order has a food allergy or intolerance"),
    ("Check availability", "Check availability for the requested time"),
    ("Tell the customer the estimated delivery time", "Tell the caller the estimated delivery time"),
    ("Never double-book a table", "Never book a table that is already booked"),
    ("Name the specific allergen back to the customer", "Repeat the specific allergen back to the caller"),
    ("Never promise a pickup time the kitchen hasn't confirmed", "Never promise a pickup time before the kitchen confirms it"),
]


def rule(id, kind, index, text):
    return {"id": id, "kind": kind, "index": index, "text": text}


def test_thresholds_separate_rewordings_from_other_rules():
    scores = [similarity(a, b) for a, b in REWORDED]
    others = [similarity(a, b) for p in PROCEDURES.values() for rules in p.values() for a, b in itertools.combinations(rules, 2)]
    assert min(scores) >= SAME_POSITION_MIN > max(others), (min(scores), max(others))
    assert sum(s >= ANYWHERE_MIN for s in scores) >= len(scores) // 2  # most rewordings are found even when the rule moved
    assert similarity("Check availability", "check  AVAILABILITY") == 1.0 and similarity("a", "b") == 0.0


def test_match():
    old = [rule("s1", "step", 1, "Ask for the full delivery address"), rule("s2", "step", 2, "Check the address is inside the delivery zone"),
           rule("n1", "never", 1, "Never tell the caller a delivery time"), rule("n2", "never", 2, "Never double-book a table")]
    # same position, reworded; unchanged ids continue (no link)
    new = [old[0], rule("s2b", "step", 2, "Check the address is inside the delivery zone with check_zone"), *old[2:]]
    assert [(x.rule_id, x.previous_rule_id, x.how) for x in match(old, new)] == [("s2b", "s2", "matched")]
    # a different rule at the same position: a new rule, and the old one was removed
    assert match(old, [old[0], rule("s9", "step", 2, "Offer the earliest time the kitchen can meet"), *old[2:]]) == []
    # moved and reworded: found anywhere in the procedure, above the higher threshold, one to one
    moved = [old[0], old[1], rule("n2b", "never", 1, "Never double-book a table or a booth"), rule("n1b", "never", 2, "Never tell the caller the delivery time")]
    assert sorted((x.rule_id, x.previous_rule_id) for x in match(old, moved)) == [("n1b", "n1"), ("n2b", "n2")]
    # never across kinds
    assert match(old, [rule("w1", "warning", 1, "Never double-book a table!"), *old[:3]]) == []
    # an applied suggestion knows what it edited, however different the words
    swap = [*old[:3], rule("n2c", "never", 2, "Seat every party at its own table")]
    assert match(old, swap) == []
    assert [(x.rule_id, x.how) for x in match(old, swap, {"n2": "n2c"})] == [("n2c", "suggestion")]


def lineage_rows(env):
    with Session(env.store.engine) as db:
        return {r.rule_id: r for r in db.exec(select(RuleLineage))}


def never_rows(env, sid):
    s = env.client.get(f"{WS}/sessions/{sid}").json()
    return next(r for r in s["checks"][0]["rules"] if r["kind"] == "never" and r["index"] == 1)


def rule_patterns(env):
    with Session(env.store.engine) as db:
        return list(db.exec(select(Pattern).where(Pattern.kind == "rule", Pattern.procedure_id == ALLERGEN)))


def test_the_loop_a_fix_follows_the_reworded_rule(env):
    """verdict -> suggestion applied -> publish -> sessions on the reworded rule join the same pattern, with its verdict
    and review state, and move it to fixed, then regressed."""
    for _ in range(3):
        env.send("A", release=1)
    never = next(p for p in env.client.get(f"{WS}/patterns", params={"procedure": ALLERGEN}).json()["items"] if p["rule"]["kind"] == "never")
    old_id = never["rule_id"]
    fixed = env.files[PATH].replace(f"  - {SAFE}\n", f"  - {SAFE}; say what you checked instead\n")
    env.model.edits = [fixed]
    sid = env.client.post(f"{WS}/patterns/{never['pattern_id']}/verdict", json={"verdict": "real"}).json()["suggestion"]["suggestion_id"]
    env.client.post(f"{WS}/suggestions/{sid}/apply")
    release = env.client.post(f"{WS}/releases", json={}).json()["release"]["number"]

    rows = lineage_rows(env)
    (new_id,) = rows
    row = rows[new_id]
    assert (row.previous_rule_id, row.lineage_id, row.how, row.release_id) == (old_id, old_id, "suggestion", release) and 0.5 < row.similarity < 1

    # new sessions run the reworded rule: a new rule id, in the same rule pattern, with its verdict
    clean = [env.send("CLEAN", release=release) for _ in range(10)]
    assert never_rows(env, clean[0])["rule_id"] == new_id
    assert state_of(env, never["pattern_id"]) == ("fixed", f"No findings in 10 sessions on release {release} or later.")
    back = env.send("A", release=release)
    finding = never_rows(env, back)
    assert finding["rule_id"] == new_id and finding["finding"]
    assert [p.id for p in rule_patterns(env) if p.rule_id == old_id] == [never["pattern_id"]]
    assert all(p.rule_id != new_id for p in rule_patterns(env))
    with Session(env.store.engine) as db:
        assert db.get(Finding, finding["finding_id"]).pattern_id == never["pattern_id"]
    item = next(i for i in queue(env)["items"] if i["pattern_id"] == never["pattern_id"])
    assert item["review_state"] == "regressed" and item["verdict"]["verdict"] == "real" and item["sessions"] == 4
    assert item["rule_text"].endswith("say what you checked instead")  # the rule as it reads now
    p = env.client.get(f"{WS}/patterns/{never['pattern_id']}").json()
    assert p["total_findings"] == 4 and p["rule"]["text"].endswith("instead")

    # Insights: one row across both wordings, under the current one
    window = {"start": "2026-09-30T00:00:00Z", "end": "2026-10-03T00:00:00Z"}
    ladder = env.client.get(f"{WS}/insights/procedures/{ALLERGEN}", params=window).json()["rules"]
    nevers = [r for r in ladder if r["kind"] == "never" and r["index"] == 1]
    assert len(nevers) == 1
    n1 = nevers[0]
    assert (n1["rule_id"], n1["lineage_id"], n1["wordings"], n1["reworded_in"]) == (new_id, old_id, 2, release)
    assert [(w["rule_id"], w["from_release"]) for w in n1["wording_history"]] == [(old_id, 1), (new_id, release)]
    assert (n1["n"], n1["fails"], n1["findings"]) == (14, 4, 4)
    for rid in (old_id, new_id):  # the rule page answers for either wording
        page = env.client.get(f"{WS}/insights/procedures/{ALLERGEN}/rules/{rid}", params=window).json()
        assert (page["rule_id"], page["lineage_id"], page["reworded_in"], page["overall"]["n"]) == (new_id, old_id, release, 14)
        assert {w["rule_id"] for w in page["wordings"]} == {old_id, new_id} and [x["pattern_id"] for x in page["patterns"]] == [never["pattern_id"]]
        assert len(page["recent_findings"]) == 4
    cmp = env.client.get(f"{WS}/insights/releases/compare", params={**window, "a": 1, "b": release}).json()
    c = next(r for r in cmp["rules"] if r["lineage_id"] == old_id)
    assert (c["rule_id"], c["rule_change"], c["a"]["n"], c["b"]["n"], c["a_text"]) == (new_id, "changed", 3, 11, SAFE)
    assert c["reworded_in"] == release and [(w["rule_id"], w["from_release"]) for w in c["wording_history"]] == [(old_id, 1), (new_id, release)]
    # the sessions behind a rule: every wording
    drill = env.client.get(f"{WS}/sessions", params={"procedure": ALLERGEN, "rule": old_id, "rule_status": ["violated"]}).json()["items"]
    assert len(drill) == 4

    # a later suggestion on the same pattern edits the rule as it reads now
    verdict(env, never["pattern_id"], "sop_wrong")
    env.model.edits = [fixed.replace("instead", "instead, every time")]
    s = env.client.post(f"{WS}/patterns/{never['pattern_id']}/suggest").json()
    s = env.client.get(f"{WS}/suggestions/{s['suggestion_id']}").json()
    assert s["status"] == "proposed" and s["rule_id"] == new_id
    assert env.model.suggest_inputs[-1]["rule"].endswith("say what you checked instead")


def test_a_hand_rewording_and_a_moved_rule_are_matched_at_publish(env):
    env.send("A")
    old = env.files[PATH]
    edited = old.replace("  - Ask if anyone in the order has a food allergy\n", "  - Ask whether anyone in the order has a food allergy or intolerance\n")
    # never 1 and 2 swapped, never 1 reworded
    edited = edited.replace(f"  - {SAFE}\n  - text: Never place the order before allergens are confirmed\n    tool: place_order\n",
                            "  - text: Never place the order before allergens are confirmed\n    tool: place_order\n  - Never say an item is safe or allergen-free\n")
    assert edited.count("Never say an item is safe") == 1
    env.store.edit("demo", {PATH: edited})
    release = env.client.post(f"{WS}/releases", json={}).json()["release"]["number"]
    rows = lineage_rows(env)
    assert sorted((r.how, r.release_id) for r in rows.values()) == [("matched", release), ("matched", release)]
    texts = {r["rule_id"]: r["text"] for r in env.store.lineage.get("demo", next(iter(rows)))["rules"]}
    assert len(texts) == 2
    # publishing again changes nothing; rolling back and publishing the old text again doesn't make a cycle
    assert env.client.post(f"{WS}/releases", json={}).status_code in (200, 201)
    env.store.edit("demo", {PATH: old})
    env.client.post(f"{WS}/releases", json={})
    assert lineage_rows(env).keys() == rows.keys()


def test_a_suppressing_verdict_judged_the_old_wording(env):
    """acceptable on a rule, then the rule is reworded: findings on the new wording count, and the pattern needs review
    until a verdict confirms it."""
    for _ in range(2):
        env.send("A")
    never = next(p for p in rule_patterns(env) if p.name == SAFE)
    verdict(env, never.id, "acceptable")
    env.store.edit("demo", {PATH: env.files[PATH].replace(f"  - {SAFE}\n", f"  - {SAFE}, even if they insist\n")})
    release = env.client.post(f"{WS}/releases", json={}).json()["release"]["number"]
    assert state_of(env, never.id)[0] == "closed"
    new = never_rows(env, env.send("A", release=release))
    assert new["finding"] and not new["suppressed"]
    assert state_of(env, never.id) == ("needs_review", "The rule was reworded after this verdict. Check it still applies: 1 finding on the new wording counts "
                                                       "until you give the verdict again, or another.")
    got = env.client.get(f"{WS}/patterns/{never.id}").json()
    assert got["review_reason"] == "reworded"
    verdict(env, never.id, "acceptable")
    assert state_of(env, never.id)[0] == "closed" and never_rows(env, env.send("A", release=release))["suppressed"]


def test_link_and_unlink_by_hand_and_rebuild(env, capsys):
    for _ in range(2):
        env.send("A")
    never = next(p for p in rule_patterns(env) if p.name == SAFE)
    verdict(env, never.id, "real")
    env.store.edit("demo", {PATH: env.files[PATH].replace(f"  - {SAFE}\n", f"  - {SAFE}, even if they insist\n")})
    release = env.client.post(f"{WS}/releases", json={}).json()["release"]["number"]
    new = env.send("A", release=release)
    new_id = never_rows(env, new)["rule_id"]
    assert lineage_rows(env)[new_id].how == "matched" and len(rule_patterns(env)) == 1 and rule_patterns(env)[0].finding_count == 3

    # unlink: the reworded rule is a new rule; its findings move to a pattern of their own, without the verdict
    r = env.client.delete(f"{WS}/rules/{new_id}/lineage")
    assert r.status_code == 200, r.text
    assert (r.json()["lineage_id"], r.json()["how"], r.json()["previous_rule_id"]) == (new_id, "manual", None)
    pats = {p.rule_id: p for p in rule_patterns(env)}
    assert set(pats) == {never.rule_id, new_id} and pats[never.rule_id].finding_count == 2 and pats[new_id].finding_count == 1
    assert env.client.get(f"{WS}/patterns/{pats[new_id].id}").json()["review_state"] == "needs_review"
    # rebuilding keeps a person's call
    assert env.client.post(f"{WS}/rules/lineage/rebuild").json()["links"] == 0
    assert lineage_rows(env)[new_id].how == "manual" and len(rule_patterns(env)) == 2

    # link again: the patterns merge into the older one, with both verdict histories (the latest counts)
    verdict(env, pats[new_id].id, "acceptable")
    r = env.client.post(f"{WS}/rules/{new_id}/lineage", json={"previous_rule_id": never.rule_id})
    assert r.status_code == 200 and r.json()["lineage_id"] == never.rule_id and [x["rule_id"] for x in r.json()["rules"]] == [never.rule_id, new_id]
    (merged,) = rule_patterns(env)
    assert merged.id == never.id and merged.finding_count == 3
    with Session(env.store.engine) as db:
        assert {v.verdict for v in db.exec(select(Verdict).where(Verdict.pattern_id == never.id))} == {"real", "acceptable"}
        assert all(f.suppressed for f in db.exec(select(Finding).where(Finding.pattern_id == never.id)))  # acceptable is the latest
    assert env.client.post(f"{WS}/rules/{never.rule_id}/lineage", json={"previous_rule_id": new_id}).status_code == 409  # a cycle
    assert env.client.post(f"{WS}/rules/nope/lineage", json={"previous_rule_id": new_id}).status_code == 404

    # data from before lineage: two rule patterns for one rule. A rebuild finds the rewording and merges them.
    with Session(env.store.engine) as db, db.begin():
        db.exec(text("DELETE FROM rule_lineage"))
    with Session(env.store.engine) as db, db.begin():
        rekey(db, "demo", ALLERGEN)
    assert len(rule_patterns(env)) == 2
    url = env.store.engine.url.render_as_string(hide_password=False)
    cli(["lineage", "rebuild", "--database-url", url, "--workspace", "demo"])
    assert "demo: 1 rules linked to an earlier wording" in capsys.readouterr().out
    (merged,) = rule_patterns(env)
    assert merged.rule_id == never.rule_id and merged.finding_count == 3 and lineage_rows(env)[new_id].how == "matched"
    assert env.store.lineage.rebuild("demo")["links"] == 1 and len(rule_patterns(env)) == 1  # idempotent


def test_calibration_labels_follow_the_rule(env, monkeypatch):
    for _ in range(3):
        env.send("A")
    never = next(p for p in rule_patterns(env) if p.name == SAFE)
    env.store.edit("demo", {PATH: env.files[PATH].replace(f"  - {SAFE}\n", f"  - {SAFE}, even if they insist\n")})
    release = env.client.post(f"{WS}/releases", json={}).json()["release"]["number"]
    new_id = never_rows(env, [env.send("A", release=release) for _ in range(3)][-1])["rule_id"]
    verdict(env, never.id, "real")
    seen = {}

    def fit(program, examples, *a, **kw):
        seen["failing"] = [set(e.failing) for e in examples]
        return {"train_score_before": 0.5, "train_score": 0.5, "fitted": []}

    monkeypatch.setattr(programs, "calibrate", fit)
    (p,) = env.client.post(f"{WS}/calibrate", json={"dry_run": True}).json()["procedures"]
    assert p["labels"] == 6 and p["skipped"] is None
    assert seen["failing"] == [{new_id}] * 6  # labels on the old wording count for the rule as the latest release words it
    assert never.rule_id != new_id


def test_the_migration_backfills_lineage(tmp_path, files, monkeypatch):
    if not SOPC_BIN:
        pytest.skip("needs sopc")
    url = fresh_db_url(tmp_path)
    store = Store(url, Sopc(SOPC_BIN))
    store.replace("demo", files)
    store.release("demo")
    store.edit("demo", {PATH: files[PATH].replace(f"  - {SAFE}\n", f"  - {SAFE}, even if they insist\n")})
    store.release("demo")
    downgrade(store.engine, "0012")  # as if the database predates the table
    monkeypatch.setenv("SOPC_BIN", SOPC_BIN)
    again = Store(url, Sopc(SOPC_BIN))
    with Session(again.engine) as db:
        rows = list(db.exec(select(RuleLineage)))
    assert [(r.procedure_id, r.how, r.release_id) for r in rows] == [(ALLERGEN, "matched", 2)]
