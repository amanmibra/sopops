"""Step 8: the checklist. Asks sopops's API what it found and compares it with out/ground_truth.json.

    python examples/walkthrough/verify.py [--models fake|real]

Prints PASS or FAIL per check and exits 1 if any failed. In fake mode the scripted checker knows the generator's
phrasings, so the findings must match the ground truth exactly. In real mode a model reads the calls: findings per
rule need 70% recall and precision, and the "safe" disclaimers (planted as a trap for a naive checker) should mostly
not be flagged.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from typing import Any

from common import WS, all_sessions, call, client, load, match_modes, wait_until_up

FAILING = {"skipped", "violated", "unheeded", "out_of_order"}
SPLIT_AT = 8  # sopops_server.patterns.SPLIT_AT

results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((ok, what))
    print(f"{'PASS' if ok else 'FAIL'}  {what}", flush=True)


def note(what: str) -> None:
    print(f"      {what}", flush=True)


def failing_by_rule(http: Any, sessions: list[dict[str, Any]]) -> dict[str, set[str]]:
    """{procedure/rule key: external ids of sessions where the checker failed that rule}."""
    out: dict[str, set[str]] = defaultdict(set)
    for s in sessions:
        detail = call(http, "GET", f"/v1/workspaces/{{ws}}/sessions/{s['id']}")
        for pc in detail.get("checks") or []:
            with_options = {r["option_of"] for r in pc.get("rules") or [] if r.get("option_of")}  # their options carry the failure
            for r in pc.get("rules") or []:
                if r["status"] in FAILING and r["rule_id"] not in with_options:
                    out[f"{pc['procedure_id']}/{r['kind']}_{r['index']}"].add(s["external_id"])
    return out


def findings_vs_truth(truth: dict[str, Any], kind: str, got: dict[str, set[str]], real: bool) -> None:
    expected: dict[str, set[str]] = defaultdict(set)
    traps: dict[str, set[str]] = defaultdict(set)
    for rule, modes in truth["expected"][kind].items():
        for mode, spec in modes.items():
            (traps if real and truth["modes"][mode].get("checker") == "naive" else expected)[rule] |= set(spec["sessions"])
    floor = 0.7 if real else 1.0
    for rule in sorted(set(expected) | set(got)):
        want, have = expected.get(rule, set()), got.get(rule, set()) - traps.get(rule, set())
        hit = len(want & have)
        recall = hit / len(want) if want else 1.0
        precision = hit / len(have) if have else 1.0
        check(recall >= floor and precision >= floor,
              f"{kind}: {rule}: {len(have)} flagged, {len(want)} planted (recall {recall:.0%}, precision {precision:.0%})")
    for rule, trap in traps.items():
        flagged = len(trap & got.get(rule, set()))
        check(flagged <= len(trap) * 0.3, f"{kind}: {rule}: {flagged} of {len(trap)} 'safe' disclaimers flagged (a careful reviewer flags few)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", choices=["fake", "real"], default=os.environ.get("WALKTHROUGH_MODELS", "fake"))
    args = parser.parse_args()
    real = args.models == "real"
    wait_until_up()
    truth = load("ground_truth.json")
    review = load("review.json")
    kinds = {sid: s["set"] for sid, s in truth["sessions"].items()}
    with client() as http:
        print("== Sessions")
        sessions = [s for s in all_sessions(http) if s["external_id"] in kinds]
        by_kind = {k: [s for s in sessions if kinds[s["external_id"]] == k] for k in ("backfill", "new")}
        planned = {k: sum(1 for v in kinds.values() if v == k) for k in by_kind}
        for k, rows in by_kind.items():
            check(len(rows) == planned[k], f"{k}: {len(rows)} of {planned[k]} sessions ingested")
        bad = [s for s in sessions if s["status"] not in ("checked", "excluded", "incomplete")]
        check(not bad, f"every session checked ({len(bad)} not: {sorted({s['status'] for s in bad})})" if bad else "every session checked")
        releases = call(http, "GET", "/v1/workspaces/{ws}/releases")
        latest = max(r["number"] for r in releases)
        check(all(s["release_id"] == 1 for s in by_kind["backfill"]), "backfill: stamped release 1")
        check(latest >= 2 and all(s["release_id"] == latest for s in by_kind["new"]), f"new: stamped release {latest} (fetched by the SDK at call start)")

        print("\n== Findings per rule against the ground truth")
        findings_vs_truth(truth, "backfill", failing_by_rule(http, by_kind["backfill"]), real)
        findings_vs_truth(truth, "new", failing_by_rule(http, by_kind["new"]), real)

        print("\n== Patterns against the planted failure modes")
        matched = match_modes(http, truth)
        floor = 0.5 if real else 0.95
        for mode, spec in truth["modes"].items():
            planted = truth["expected"]["backfill"].get(f"{spec['procedure']}/{spec['rule']}", {}).get(mode)
            if not planted or planted["count"] < 2 or (real and spec.get("checker")):
                continue
            m = matched.get(mode)
            check(m is not None and m["jaccard"] >= floor,
                  f"{mode} ({planted['count']} calls): " + (f"pattern '{m['pattern']['name']}', overlap {m['jaccard']:.0%}" if m else "no pattern"))
        for rule, modes in truth["expected"]["backfill"].items():
            real_modes = {m: v for m, v in modes.items() if not (real and truth["modes"][m].get("checker"))}
            if len(real_modes) > 1 and sum(v["count"] for v in real_modes.values()) >= SPLIT_AT:
                ids = {matched[m]["pattern"]["pattern_id"] for m in real_modes if m in matched}
                check(len(ids) == len(real_modes), f"{rule}: split into {len(ids)} failure modes ({len(real_modes)} planted)")

        print("\n== Verdicts")
        for mode, v in review["verdicts"].items():
            spec = truth["modes"][mode]
            p = call(http, "GET", f"/v1/workspaces/{{ws}}/patterns/{v['pattern_id']}", params={"window": "365d"})
            if spec["verdict"] in ("acceptable", "checker_wrong"):
                check(v["suppressed"] > 0 and v["suppressed"] >= truth["expected"]["backfill"][f"{spec['procedure']}/{spec['rule']}"][mode]["count"] * (0.7 if real else 1),
                      f"{mode}: '{p['name']}' is {spec['verdict']}: {v['suppressed']} findings suppressed")
            else:
                check(v["suppressed"] == 0, f"{mode}: '{p['name']}' is {spec['verdict']}: its {p['total_findings']} findings still count")

        for mode, v in review["verdicts"].items():
            if "regression_cases" in v:
                check(v["regression_cases"] > 0, f"{mode}: {v['regression_cases']} regression cases exported (out/regression-{mode}.jsonl)")

        print("\n== Suggestions")
        for mode, sid in review["suggestions"].items():
            s = call(http, "GET", f"/v1/workspaces/{{ws}}/suggestions/{sid}")
            check(s["status"] in ("applied", "pr_open"), f"{mode}: suggestion for {s['block_id']} {s['status']}: {s['rationale']}")
            check(bool(s["new_text"]) and s["new_text"] != s["old_text"], f"{mode}: the edit changes {s['path']} and compiled with sop")
            got = call(http, "GET", f"/v1/workspaces/{{ws}}/releases/{latest}")
            check(any(f["path"] == s["path"] and f["version"] > (s["base_version"] or 0) for f in files_of(got)), f"{mode}: release {latest} has the edited {s['path']}")
        if len(review["suggestions"]) < 2:
            check(False, f"two suggestions made ({len(review['suggestions'])})")

        print("\n== Insights: release 1 against release " + str(latest))
        cmp = call(http, "GET", "/v1/workspaces/{ws}/insights/releases/compare", params={"a": 1, "b": latest, "window": "30d"})
        rates = {p["procedure_id"]: p for p in cmp["procedures"]}
        p = rates.get("allergen-check")
        ok = p is not None and None not in (p["a"]["pass_rate"], p["b"]["pass_rate"]) and p["b"]["pass_rate"] > p["a"]["pass_rate"]
        check(ok, f"allergen-check (the fix): pass rate {p['a']['pass_rate']}% (n={p['a']['n']}) -> {p['b']['pass_rate']}% (n={p['b']['n']}), {p['flag']}"
              if p else "allergen-check: not in the comparison")
        d = rates.get("delivery-orders")
        if d:
            note(f"delivery-orders: {d['a']['pass_rate']}% -> {d['b']['pass_rate']}% ({d['flag']}); release 1's delivery-time findings are "
                 "suppressed by the acceptable verdict, so the rewording shows in the findings above, not here")
        never = next((r for r in cmp["rules"] if r["procedure_id"] == "allergen-check" and r["kind"] == "never" and r["index"] == 1), None)
        if never:
            note(f"allergen-check never 1: {never['a'].get('pass_rate')}% -> {never['b'].get('pass_rate')}%")

        print("\n== Reworded rules keep their history")
        reworded = [r for r in cmp["rules"] if r["rule_change"] == "changed"]
        check(bool(reworded), f"release {latest} rewords {len(reworded)} rule(s) the suggestions edited")
        for r in reworded:
            key = f"{r['procedure_id']}/{r['kind']}_{r['index']}"
            lin = call(http, "GET", f"/v1/workspaces/{{ws}}/rules/{r['rule_id']}/lineage", params={"procedure": r["procedure_id"]})
            check(lin["lineage_id"] == r["lineage_id"] != r["rule_id"] and lin["previous_rule_id"] == r["lineage_id"] and lin["how"] == "suggestion",
                  f"{key}: the new wording continues rule {r['lineage_id']} (linked by the applied suggestion, not by guessing)")
            check(r["a"]["n"] > 0 and r["b"]["n"] > 0 and r["a_text"] and r["a_text"] != r["text"],
                  f"{key}: one row in release compare across both wordings (n={r['a']['n']} on “{r['a_text']}”, n={r['b']['n']} on “{r['text']}”)")
            ladder = call(http, "GET", f"/v1/workspaces/{{ws}}/insights/procedures/{r['procedure_id']}", params={"window": "30d"})["rules"]
            row = next((x for x in ladder if x["lineage_id"] == r["lineage_id"]), None)
            check(row is not None and row["rule_id"] == r["rule_id"] and row["wordings"] == 2 and row["reworded_in"] == latest,
                  f"{key}: the rule ladder shows it once, under the new wording, reworded in release {latest}")
            pats = call(http, "GET", "/v1/workspaces/{ws}/patterns", params={"procedure": r["procedure_id"], "window": "365d"})["items"]
            mine = [p for p in pats if p["rule_id"] == r["lineage_id"]]
            planted = sum(v["count"] for v in truth["expected"]["backfill"].get(key, {}).values()) + sum(v["count"] for v in truth["expected"]["new"].get(key, {}).values())
            got = mine[0]["total_findings"] if len(mine) == 1 else 0
            check(len(mine) == 1 and (got >= planted * 0.7 if real else got == planted) and not any(p["rule_id"] == r["rule_id"] for p in pats),
                  f"{key}: one rule pattern for both wordings, {got} findings ({planted} planted across releases 1 and {latest})")
            family = {x["pattern_id"] for p in mine for x in (p, *p["sub_patterns"])}
            judged = [m for m, v in review["verdicts"].items() if f"{truth['modes'][m]['procedure']}/{truth['modes'][m]['rule']}" == key]
            check(all(review["verdicts"][m]["pattern_id"] in family for m in judged),
                  f"{key}: its verdicts ({', '.join(judged) or 'none'}) are on that pattern, so they follow the new wording")

        print("\n== Calibration (a preview: nothing is saved)")
        res = http.post(f"/v1/workspaces/{WS}/calibrate", json={"dry_run": True})
        check(res.status_code == 200, f"calibrate answers ({res.status_code}{': ' + res.text[:300] if res.status_code != 200 else ''})")
        if res.status_code == 200:
            fitted = [p for p in res.json()["procedures"] if not p["skipped"]]
            check(bool(fitted), f"{len(fitted)} procedure(s) fitted to the verdicts: " + ", ".join(f"{p['procedure_id']} ({p['labels']} labeled sessions)" for p in fitted))

    failed = [w for ok, w in results if not ok]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed" + (f"; failed: {len(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)


def files_of(release: dict[str, Any]) -> list[dict[str, Any]]:
    """A release's files as [{path, version}], whichever shape the API gives them in."""
    files = release.get("files") or release.get("release", {}).get("files") or {}
    return [{"path": k, "version": v} for k, v in files.items()] if isinstance(files, dict) else files


if __name__ == "__main__":
    main()
