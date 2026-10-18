"""Step 2, without a coding agent: onboard the sample agent repo into sopops with a prepared proposal.

    python examples/walkthrough/onboard.py push      # an agent import, progress, the proposal: review it in the UI
    python examples/walkthrough/onboard.py publish   # resolve the drift, apply as drafts, publish release 1
    python examples/walkthrough/onboard.py           # both

`push` does over HTTP what Claude Code does through sopops-mcp when you run the onboarding one-liner: it starts an
agent import (the onboarding token the Import page gives you), reports progress, and pushes a proposal with each
agent's original prompt. The proposal is onboarding/proposal.json, what a coding agent makes of agent/before/; the
originals are built by agent/before/prompts.py itself, so fidelity is checked against the real prompts.

`publish` is what the reviewer does in the UI: pick the shared wording where the restaurants drifted, apply, publish.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from types import ModuleType

from common import HERE, URL, WS, call, client, load, save, wait_until_up

PROPOSAL = HERE / "onboarding" / "proposal.json"
PROMPTS = HERE / "agent" / "before" / "prompts.py"
DRIFT_CHOICE = "bayleaf-bistro"  # the reviewer keeps the shared wording of the allergen never rule


def prompts_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("harbor_lane_prompts", PROMPTS)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


def originals() -> dict[str, str]:
    """Each agent's prompt exactly as the agent repo builds it today."""
    prompts = prompts_module()
    return {name: prompts.build_prompt(name) for name in prompts.RESTAURANTS}


def push() -> None:
    wait_until_up()
    with client() as http:
        started = call(http, "POST", "/v1/workspaces/{ws}/imports/agent", {"mode": "hosted", "server_url": URL, "author": "walkthrough"})
        imp, token = started["import"]["id"], started["token"]
        agent = {"Authorization": f"Bearer {token}"}
        steps = [
            ("find", "Found 4 restaurant agents in agent/before: prompts.py builds each prompt, agent.py deploys it per AGENT_NAME."),
            ("extract", "Split the shared sections into 3 shared instructions and 3 procedures; restaurant names, allergen links and transfer targets became variables."),
            ("drift", "Copper Wok's allergen section is an older hand-edited copy: its first never rule is worded differently."),
            ("fidelity", "Checked every agent's compiled prompt against its original."),
        ]
        for step, message in steps:
            call(http, "POST", "/v1/onboarding/progress", {"step": step, "message": message}, headers=agent)
        prepared = json.loads(PROPOSAL.read_text())
        got = call(http, "PUT", "/v1/onboarding/proposal", {**prepared, "originals": originals()}, headers=agent)
    save("import.json", {"id": imp})
    for f in got["fidelity"]:
        print(f"  {f['agent']}: fidelity {round(f['score'] * 100)}%{'' if f['ok'] else ' (see the review)'}")
    print(f"Pushed the proposal. Review it: {URL}/w/{WS}/import/{imp}")


def publish() -> None:
    wait_until_up()
    imp = load("import.json")["id"]
    with client() as http:
        got = call(http, "GET", f"/v1/workspaces/{{ws}}/imports/{imp}")
        drift = [f for f in got["proposal"]["findings"] if f["kind"] == "drift" and not f.get("resolved")]
        for f in drift:
            call(http, "POST", f"/v1/workspaces/{{ws}}/imports/{imp}/findings/{f['id']}", {"choice": DRIFT_CHOICE})
            print(f"Resolved '{f['title']}': everyone gets {DRIFT_CHOICE}'s wording.")
        applied = call(http, "POST", f"/v1/workspaces/{{ws}}/imports/{imp}/apply", {"note": "Onboarded from the Harbor Lane agent repo"})
        print(f"Applied as drafts: {len(applied['changed'])} files.")
        got = call(http, "POST", "/v1/workspaces/{ws}/releases", {"note": "Onboarded from the agent repo"})
    print(f"Published release {got['release']['number']}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", nargs="?", choices=["push", "publish", "all"], default="all")
    args = parser.parse_args()
    if args.step in ("push", "all"):
        push()
    if args.step in ("publish", "all"):
        publish()


if __name__ == "__main__":
    main()
