"""Step 10: the fixes hold. Publish release 3 and play a few more weeks' worth of calls in which they did.

    python examples/walkthrough/followup.py [--calls 60]

Step 9 leaves edits to two procedures as drafts: the delivery-zone fix (with the reworded delivery-time rule) and the
allergen-list and anaphylaxis fixes. This publishes them as release 3, then plays `--calls` new calls through the
LiveKit adapter in which those mistakes don't happen any more (`RATES["followup"]` in generate.py). After
`FIXED_MIN_N` clean sessions on a procedure a fix counts as fixed, so Mistakes → Fixed shows the delivery-zone
mistake fixed after the 20-odd delivery calls among them, with the count.

Then it leaves the loop running, the way a team's workspace looks on an ordinary day: sopops writes a fix for the
large-party reservations mistake (a real mistake with no fix yet), and it's applied as a draft that isn't published.
The demo (record_demo.py) records the workspace after this step.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from common import (
    OUT,
    call,
    client,
    ingest_token,
    load,
    match_modes,
    wait_for_pipeline,
    wait_until_up,
)
from generate import jobs
from replay import replay
from review import wait_for

RELEASE_NOTE = "Delivery-zone check and allergen-list fixes (from review)"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--calls", type=int, default=60)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    wait_until_up()
    if (OUT / "followup.jsonl").exists():
        sys.exit(
            "followup.py has run on this database already (out/followup.jsonl); start from a fresh walkthrough to run it again"
        )
    with client() as http:
        draft = call(http, "GET", "/v1/workspaces/{ws}/draft")["items"]
        if draft:
            got = call(
                http,
                "POST",
                "/v1/workspaces/{ws}/releases",
                {"note": RELEASE_NOTE, "author": "walkthrough"},
            )
            print(
                f"Published release {got['release']['number']}: {', '.join(i['id'] for i in draft)}."
            )

        followup = jobs(args.seed, args.calls, "followup", None)
        (OUT / "followup.jsonl").write_text(
            "".join(json.dumps(j) + "\n" for j in followup)
        )
        before = sum(1 for _ in (OUT / "backfill.jsonl").open()) + sum(
            1 for _ in (OUT / "new.jsonl").open()
        )
        releases = asyncio.run(
            replay(followup, ingest_token(http), rate=50, speed=2000, concurrency=10)
        )
        print(
            f"{len(followup)} calls ({sum('delivery-orders' in j['procedures'] for j in followup)} delivery orders) on "
            + ", ".join(
                f"release {r}: {n}"
                for r, n in sorted(releases.items(), key=lambda kv: kv[0] or 0)
            )
        )
        done = wait_for_pipeline(http, before + len(followup))
        print("checked: " + ", ".join(f"{v} {k}" for k, v in sorted(done.items())))

        # A fix in progress: the large-party mistake got a "real" verdict in step 6 but no fix. Ask for one, apply it.
        large = match_modes(http, load("ground_truth.json")).get("large_party")
        if large is None:
            sys.exit("no pattern matches the large-party mistake")
        s = call(
            http,
            "POST",
            f"/v1/workspaces/{{ws}}/patterns/{large['pattern']['pattern_id']}/suggest",
            {},
        )
        s = wait_for(http, s["suggestion_id"])
        if s["status"] != "proposed":
            sys.exit(f"the large-party suggestion is {s['status']}: {s.get('error')}")
        applied = call(
            http,
            "POST",
            f"/v1/workspaces/{{ws}}/suggestions/{s['suggestion_id']}/apply",
            {"reviewer": "walkthrough"},
        )
        print(f"Large-party fix ({s['block_id']}): {applied['status']}, not published.")

        for p in call(
            http, "GET", "/v1/workspaces/{ws}/review", params={"state": "fixed"}
        )["items"]:
            print(f"  fixed: {p['name']}: {p['review_note']}")


if __name__ == "__main__":
    main()
