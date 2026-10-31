"""Step 11: start a spot-check batch on Fine tune, half answered, so the page has something in it.

    python examples/walkthrough/spot_checks.py

Fine tune is hosted sopops's (serve.py turns it on for the walkthrough). It asks about rules the checker passed, one
batch at a time. This draws a batch and answers some of it the way a reviewer would on the page: most are
confirmations ("Was the checker right? Yes"), one says the checker was wrong (a step it called done that didn't apply
to the call), with a note. The rest are left for you to answer in the UI.

The correction gives a status that isn't a finding, so it changes no pattern or rate the earlier steps check.
"""

from __future__ import annotations

from common import call, client, wait_until_up

BATCH = 25
CONFIRM = 5  # answered "the checker was right"
NOTE = "Nothing in this call needed the step, so it didn't apply."


def answer(http, item, status, note=None) -> None:
    call(http, "POST", f"/v1/workspaces/{{ws}}/sessions/{item['session_id']}/rules/{item['procedure_id']}/{item['rule_id']}/label",
         {"status": status, "note": note, "reviewer": "Sam", "source": "spot_check"})


def main() -> None:
    wait_until_up()
    with client() as http:
        batch = call(http, "POST", "/v1/workspaces/{ws}/spot-checks/batches", {"size": BATCH})
        items = [i for i in batch["items"] if not i["answered"]]
        wrong = next((i for i in items if i["kind"] == "step" and i["status"] == "done" and "not_applicable" in i["statuses"]), None)
        if wrong:
            answer(http, wrong, "not_applicable", NOTE)
        confirmed = [i for i in items if i is not wrong][-CONFIRM:]  # from the end: the cards up top stay open
        for i in confirmed:
            answer(http, i, i["status"])
        left = call(http, "GET", "/v1/workspaces/{ws}/spot-checks")
        print(f"spot checks: {left['answered']} of {left['size']} answered ({left['corrections']} saying the checker was wrong); the rest are on Fine tune")


if __name__ == "__main__":
    main()
