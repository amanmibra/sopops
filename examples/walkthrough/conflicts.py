"""Step 9: two suggestions on one procedure, written before either is applied.

    python examples/walkthrough/conflicts.py [--models fake|real]

After release 2, a reviewer asks for two more suggestions on each of two procedures, then applies the first of each pair:

| Procedure | Suggestions | The second, after the first is applied |
|---|---|---|
| delivery-orders | guidance for "no zone check" (real), then an exception on the delivery-time rule (eta, acceptable) | `merges`: the edits are on different lines; applying writes both |
| allergen-check | guidance for "no lookup", then guidance for "no transfer" (both real) | `conflict`: both add a paragraph at the same place; apply is refused; **Rewrite against the current text** writes it again, keeping the first |

The edits stay as unpublished drafts (Instructions -> Changes). In fake mode the scripted reviewer makes the edits
above, so each state is checked; in real mode the states are printed, since a real model's edits may land elsewhere.
Prints PASS or FAIL per check and exits 1 if any failed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

from common import WS, call, client, load, match_modes, wait_until_up

results: list[tuple[bool, str]] = []


def check(ok: bool, what: str, strict: bool = True) -> None:
    if not strict:
        print(f"      {what}", flush=True)
        return
    results.append((ok, what))
    print(f"{'PASS' if ok else 'FAIL'}  {what}", flush=True)


def wait_for(http: Any, suggestion_id: str, seconds: float = 300) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    while True:
        got = call(http, "GET", f"/v1/workspaces/{{ws}}/suggestions/{suggestion_id}")
        if got["status"] != "generating":
            return got
        if time.monotonic() > deadline:
            sys.exit(f"suggestion {suggestion_id} is still generating after {seconds:.0f}s")
        time.sleep(1)


def suggest(http: Any, pattern_id: str) -> dict[str, Any]:
    s = wait_for(http, call(http, "POST", f"/v1/workspaces/{{ws}}/patterns/{pattern_id}/suggest")["suggestion_id"])
    if s["status"] != "proposed":
        sys.exit(f"the suggestion for pattern {pattern_id} is {s['status']}: {s.get('error')}")
    return s


def apply(http: Any, suggestion_id: str) -> Any:
    """The response as it is: a 409 is one of the outcomes checked."""
    return http.post(f"/v1/workspaces/{WS}/suggestions/{suggestion_id}/apply", json={"reviewer": "walkthrough"})


def pair(http: Any, matched: dict[str, Any], first: str, second: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if first not in matched or second not in matched:
        print(f"  no pattern for {first if first not in matched else second}: skipped")
        return None
    a, b = suggest(http, matched[first]["pattern"]["pattern_id"]), suggest(http, matched[second]["pattern"]["pattern_id"])
    print(f"  {first}: {a['rationale']}\n  {second}: {b['rationale']}")
    check(a["freshness"] == b["freshness"] == "current", f"{a['procedure_id']}: both suggestions are current before either is applied")
    res = apply(http, a["suggestion_id"])
    check(res.status_code == 200 and res.json()["status"] == "applied", f"{a['procedure_id']}: the {first} suggestion is applied as a draft")
    return a, b


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", choices=["fake", "real"], default=os.environ.get("WALKTHROUGH_MODELS", "fake"))
    args = parser.parse_args()
    fake = args.models == "fake"
    wait_until_up()
    with client() as http:
        matched = match_modes(http, load("ground_truth.json"))

        print("\n== Two suggestions that merge (delivery-orders)")
        got = pair(http, matched, "no_zone", "eta")
        if got:
            _, b = got
            b = call(http, "GET", f"/v1/workspaces/{{ws}}/suggestions/{b['suggestion_id']}")
            check(b["freshness"] == "merges", f"eta: {b['freshness']} with the applied one", fake)
            if b["freshness"] == "merges":
                res = apply(http, b["suggestion_id"])
                done = res.json()
                text = call(http, "GET", f"/v1/workspaces/{{ws}}/files/{b['path']}")["content"]
                check(res.status_code == 200 and done["merged_into_version"] == b["since_written"]["head_version"] and text == b["since_written"]["merged_text"],
                      f"eta: applied, merged into version {done.get('merged_into_version')} of {b['path']}, which now has both edits")

        print("\n== Two suggestions that conflict (allergen-check)")
        got = pair(http, matched, "no_lookup", "no_transfer")
        if got:
            _, d = got
            d = call(http, "GET", f"/v1/workspaces/{{ws}}/suggestions/{d['suggestion_id']}")
            conflicts = (d.get("since_written") or {}).get("conflicts") or []
            check(d["freshness"] == "conflict" and len(conflicts) == 1, f"no_transfer: {d['freshness']}, {len(conflicts)} conflicting part(s)", fake)
            if d["freshness"] == "conflict":
                res = apply(http, d["suggestion_id"])
                check(res.status_code == 409, f"no_transfer: apply refused ({res.status_code}): {res.json().get('detail', '')[:120]}")
                current = call(http, "GET", f"/v1/workspaces/{{ws}}/files/{d['path']}")["content"]
                new = call(http, "POST", f"/v1/workspaces/{{ws}}/suggestions/{d['suggestion_id']}/rewrite", {"reviewer": "walkthrough"})
                new = wait_for(http, new["suggestion_id"])
                old = call(http, "GET", f"/v1/workspaces/{{ws}}/suggestions/{d['suggestion_id']}")
                print(f"  rewritten: {new['rationale']}")
                check(new["status"] == "proposed" and new["freshness"] == "current" and new["rewrite_of"] == d["suggestion_id"] and new["old_text"] == current,
                      "no_transfer: rewritten against the current text, as a new suggestion")
                check(old["status"] == "dismissed" and old["rewritten_as"] == new["suggestion_id"], f"no_transfer: the old one is dismissed: “{old['note']}”")
                res = apply(http, new["suggestion_id"])
                text = call(http, "GET", f"/v1/workspaces/{{ws}}/files/{d['path']}")["content"]
                kept = [line for line in current.splitlines() if line and line not in d["old_text"].splitlines()]
                check(res.status_code == 200 and all(line in text.splitlines() for line in kept), "no_transfer: the rewrite applies, and the first suggestion's edit is still there", fake)

    failed = [w for ok, w in results if not ok]
    print(f"\n{len(results) - len(failed)} of {len(results)} conflict checks passed" + (f"; failed: {len(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
