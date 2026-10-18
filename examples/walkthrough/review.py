"""Step 6: review the patterns, give verdicts, apply the suggested edits, publish release 2.

    python examples/walkthrough/review.py [--dry-run]

What a reviewer does on the Review page, over the API. Each planted failure mode is found among the patterns by its
sessions (names come from the model, so they differ between fake and real mode), then:

| Pattern (planted mode) | Verdict | Then |
|---|---|---|
| Told the caller a dish is safe (safe_claim) | real | a suggested edit to the allergen procedure |
| Gave the caller a delivery time (eta) | acceptable | a suggested rewording of the too-broad rule |
| Used the word "safe" while declining to promise it (safe_disclaimer; fake mode) | checker_wrong | suppressed |
| the other real ones | real | no suggestion: fixed in the agent's code or training, not the SOP |

The two suggestions are applied as drafts (or opened as pull requests, if the workspace has a repo connection), and
release 2 is published from Unpublished changes.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from common import OUT, WS, call, client, load, match_modes, save, wait_until_up

SUGGEST = {"safe_claim", "eta"}
NOTES = {
    "safe_claim": "Agents reassure callers instead of reporting what the allergen list says.",
    "eta": "Giving the time from the zone check is what we want; the rule meant before the zone check.",
    "safe_disclaimer": "The agent said it can't promise the dish is safe: that's the right answer, not a claim.",
}


def wait_for(http: Any, suggestion_id: str, seconds: float = 300) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    while True:
        got = call(http, "GET", f"/v1/workspaces/{{ws}}/suggestions/{suggestion_id}")
        if got["status"] != "generating":
            return got
        if time.monotonic() > deadline:
            sys.exit(f"suggestion {suggestion_id} is still generating after {seconds:.0f}s")
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Show what it would do.")
    args = parser.parse_args()
    wait_until_up()
    truth = load("ground_truth.json")
    done: dict[str, Any] = {"verdicts": {}, "suggestions": {}}
    with client() as http:
        matched = match_modes(http, truth)
        for mode, spec in truth["modes"].items():
            m = matched.get(mode)
            if spec.get("releases") == "2":
                continue  # only exists after release 2
            if m is None:
                print(f"  {mode}: no pattern matches it{' (expected: real reviewers judge it fine)' if spec.get('checker') else ''}")
                continue
            p = m["pattern"]
            print(f"  {mode}: '{p['name']}' ({p['findings']} findings) -> {spec['verdict']}{' + suggestion' if mode in SUGGEST else ''}")
            if args.dry_run:
                continue
            got = call(http, "POST", f"/v1/workspaces/{{ws}}/patterns/{p['pattern_id']}/verdict",
                       {"verdict": spec["verdict"], "note": NOTES.get(mode), "reviewer": "walkthrough", "suggest": mode in SUGGEST})
            done["verdicts"][mode] = {"pattern_id": p["pattern_id"], "verdict": spec["verdict"], "suppressed": got["suppressed_count"]}
            if got.get("suggestion"):
                done["suggestions"][mode] = got["suggestion"]["suggestion_id"]
        if args.dry_run:
            return
        for mode, sid in done["suggestions"].items():
            s = wait_for(http, sid)
            if s["status"] != "proposed":
                sys.exit(f"the suggestion for {mode} is {s['status']}: {s.get('error')}")
            print(f"  suggestion for {mode} ({s['block_id']}): {s['rationale']}")
            applied = call(http, "POST", f"/v1/workspaces/{{ws}}/suggestions/{sid}/apply", {"reviewer": "walkthrough"})
            print(f"    -> {applied['status']}{' ' + applied['pr_url'] if applied.get('pr_url') else ''}")
        for mode in ("safe_claim", "safe_disclaimer"):  # regression cases: replay against the fix, and re-check
            if mode in done["verdicts"]:
                res = http.get(f"/v1/workspaces/{WS}/patterns/{done['verdicts'][mode]['pattern_id']}/regression")
                res.raise_for_status()
                (OUT / f"regression-{mode}.jsonl").write_text(res.text)
                cases = done["verdicts"][mode]["regression_cases"] = len(res.text.splitlines())
                print(f"  regression cases for {mode}: {cases} in out/regression-{mode}.jsonl")
        save("review.json", done)
        if any(call(http, "GET", f"/v1/workspaces/{{ws}}/suggestions/{sid}")["status"] == "pr_open" for sid in done["suggestions"].values()):
            print("The edits are pull requests: merge them (CI publishes), then run replay.py.")
            return
        got = call(http, "POST", "/v1/workspaces/{ws}/releases", {"note": "Allergen guidance; delivery-time rule reworded (from review)", "author": "walkthrough"})
        print(f"Published release {got['release']['number']}.")


if __name__ == "__main__":
    main()
