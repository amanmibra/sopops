# 📋🗄️ Walkthrough: a LiveKit team onboards its agent and sees the whole loop

Harbor Lane (made up) runs four restaurants on one LiveKit phone agent. Its instructions live in code. In this walkthrough the team moves them into sopops, points the agent at it, sends two weeks of calls, finds what keeps going wrong, fixes it with a reviewed edit, and sees the next calls get better.

Everything here is synthetic: the restaurants, the callers, the phone numbers (555-01xx) and the calls, generated from a seed with a known answer, so you can check that sopops found what was planted.

```
walkthrough/
  agent/before/          the team's repo before sopops: prompts.py builds each restaurant's instructions in code
  agent/after/           the same agent fetching its instructions from sopops (the diff is in step 3)
  onboarding/            proposal.json: what a coding agent makes of agent/before (for the no-agent path)
  serve.py               sopops on 127.0.0.1:8585 with its own database, fake or real models
  fake_models.py         fake mode: scripted stand-ins for every model call
  onboard.py             step 2 without a coding agent
  generate.py            step 4: 300 calls and ground_truth.json
  backfill.py            step 5: bulk send through the SDK
  review.py              step 6: verdicts, suggestions, release 2
  replay.py              step 7: new calls through the LiveKit adapter, as if live
  verify.py              step 8: the pass/fail checklist
  conflicts.py           step 9: two suggestions on one procedure, one merging and one in conflict
  run_all.py, justfile   every step, or one at a time
```

## Quick start

From the repo root, with [just](https://github.com/casey/just) (`brew install just`, `cargo install just` or `uv tool install rust-just`), [sop](https://github.com/sopops-dev/sop) v0.1.0 or later on `PATH` (or `SOP_BIN`) and the UI built once (`pnpm -C web install && pnpm -C web build`):

```sh
just examples/walkthrough/all          # fake mode: no keys, no cost, about a minute
```

It starts a fresh sopops on http://127.0.0.1:8585, runs steps 2 to 9, prints the checklists, and leaves sopops running so you can look around (Ctrl-C stops it). Everything it writes is in `examples/walkthrough/out/` (`just examples/walkthrough/clean` removes it). Another port: `WALKTHROUGH_PORT=8686`.

That sopops has no sign-in, so whoever opens it is in. For the same demo behind a login:

```sh
just examples/walkthrough/all-sign-in  # uv run python examples/walkthrough/run_all.py --sign-in
```

The steps run exactly as above, with sign-in off, because publishing a release asks for a person and the scripts hold only tokens. When they finish, the same database is served again with sign-in on and an account already in it, `test@example.com` / `password123`. Sign-up is open too, so you can make an account of your own at `/sign-up`, which comes with a team of its own and so starts empty: sign in as the seeded account to see the walkthrough's calls. On a database that already holds the data, `just examples/walkthrough/sign-in` puts it behind a login without running the steps again.

To go step by step instead, run `just examples/walkthrough/fresh` in one terminal and each step's `just` recipe in another.

## The public demo

The same data, as a demo anyone can open with no account: the real UI built with `VITE_SOPOPS_DEMO=1`, answering every API call from a recording, with a guided tour of the loop that starts on the first visit. Nothing is saved there; an edit says so and points to sign-up.

```sh
WALKTHROUGH_WORKSPACE=harbor-lane just examples/walkthrough/all   # leave it running
WALKTHROUGH_WORKSPACE=harbor-lane just examples/walkthrough/demo  # step 10, record every page, build web/dist-demo
just examples/walkthrough/demo-preview                           # http://127.0.0.1:4180
```

`record_demo.py` opens every page of the workspace in Chrome (links, tabs and all 300 sessions) and writes each answer to `web/demo-data/`; `web/dist-demo/` is a static site to host anywhere that serves `index.html` for unknown paths. Record again after an API change. The tour (`web/src/components/tour.tsx`) anchors on `data-tour` attributes and form fields, so a step whose target moves shows its card mid-screen rather than breaking.

## Fake mode and real mode

| | Fake (`MODELS=fake`, the default) | Real (`MODELS=real`) |
|---|---|---|
| What answers the model calls | `fake_models.py`: a keyword checker that knows the generator's phrasings, through `sopops.ai.fakes` | your models, from the environment |
| Cost, network | none | see below |
| Findings | exactly the planted ones (verify.py expects 100%) | what the model finds (verify.py expects 70% recall and precision per rule) |
| What it shows | every page, the whole pipeline around the models (routing, checks, rollup, findings, patterns split into failure modes, assignment, themes, verdicts, suppression, suggestions compiled by sop, Insights), on data with a known answer | how well a real checker and reviewer do on the same calls |
| What it doesn't | anything about model quality: the fake can't misread a call it wasn't scripted for, and its suggestions are canned edits | |

Fake mode checks every rule with the scripted reviewer (`SOPOPS_CHECK_MODE=review`), names each failure mode after the checker's reason, and is naive on purpose about one thing: it flags the word "safe" even when the agent says it *can't* promise a dish is safe. That pattern is there for a `checker_wrong` verdict. A real reviewer should mostly not flag those calls, and verify.py checks that in real mode.

**Real mode.** Bring your own keys, in the environment of the terminal that runs `just serve` or `just all`:

```sh
export SOPOPS_MODEL_FAST=anthropic/claude-haiku-4-5         # decisions (routing, rule checks, assignment)
export SOPOPS_MODEL_REASONING=anthropic/claude-sonnet-5-5   # the reviewer: flagged rules, clustering, themes, suggestions
export ANTHROPIC_API_KEY=...                                # or the provider your model strings name
# optional: TypeSafe's Jev answers the decisions instead of the fast model
export TYPESAFE_API_KEY=...                                 # SOPOPS_JEV=off to keep it off
MODELS=real just examples/walkthrough/all
```

Any [model string DSPy knows](../../README.md#models) works, or a local model behind an OpenAI-compatible endpoint (`SOPOPS_LLM_API_BASE`). Estimated cost for the 300 calls: about 2M input tokens of decisions on the fast model and about 200 reviewer calls of 4k tokens each, plus a few clustering, theme and suggestion calls. At $1/$5 per million tokens for the fast model and $3/$15 for the reviewer that's roughly **$5 to $10**. With Jev answering the decisions (about $0.04 per million input tokens) it's roughly **$4**, nearly all of it the reviewer. A real run also takes longer: give it 10 to 20 minutes.

## The steps

Each step: the command (from the repo root), where to look in the UI, and what you should see. Routes are under http://127.0.0.1:8585.

### 1. Start sopops

```sh
just examples/walkthrough/fresh        # or: uv run python examples/walkthrough/serve.py --fresh
```

Look at `/`: an empty instance. The walkthrough uses the workspace `walkthrough`, made by the first write. `just examples/walkthrough/sign-in` serves the same database with sign-in on instead, making `test@example.com` / `password123` when nobody has an account yet. The steps below expect sign-in off, so save that for when you want to look around.

### 2. Onboard the agent repo

`agent/before` is the team's repo as it is today. `prompts.py` builds four agents' instructions from shared sections (a host role, a brand voice, allergen, delivery and reservation sections, a closing) plus each restaurant's details. Copper Wok's allergen section is an older hand-edited copy whose first "never" rule says something slightly different: drift for onboarding to find.

**With Claude Code (the documented path).** In sopops, open **Instructions → Import** (`/w/walkthrough/import`), choose **Use Claude Code**, and copy the one command it gives you. Run it inside the agent repo:

```sh
cd examples/walkthrough/agent/before
claude mcp add sopops --env SOPOPS_URL=http://127.0.0.1:8585 --env SOPOPS_TOKEN=sopops_onb_... \
  -- uvx --from 'sopops[mcp] @ git+https://github.com/sopops-dev/sopops@main#subdirectory=sdks/python' sopops-mcp \
  && claude "Onboard this project's agents into sopops: call the sopops get_kickoff tool and follow it."
```

(To run the MCP server from this clone instead of GitHub's `main`, start sopops with `SOPOPS_MCP_SOURCE="$PWD/sdks/python[mcp]"` from the repo root; the command then says `--from <your clone>/sdks/python[mcp]`.) The agent reads `prompts.py`, builds a proposal with the `sopops-mcp` tools, checks fidelity and pushes it; you watch its progress on the import page. Expect roughly what `onboarding/proposal.json` holds: 4 agents, 3 shared instructions, 3 procedures (4 until the drift is resolved), variables for the restaurant name, allergen link and who to transfer to.

This is **In sopops** (hosted) mode, which leaves the repo alone. **In my repo too** keeps the instructions in a GitHub repository as well: connect it on the same page (the form from Settings → GitHub; the folder defaults to `sops`), and sopops looks there first. A sop project sop builds is offered with **Import it**; with none, the kickoff also tells the agent to open the onboarding pull request adding `sops/` and report it, and the import page follows that PR: when you merge it, sopops pulls `sops/` into drafts and says **Publish your first release**. A folder sop can't build shows sop's errors, to import it anyway and fix it, or have the agent rewrite it. The walkthrough's sample repo isn't on GitHub, so the steps below stay hosted.

**Without a coding agent.** Push the prepared proposal over the same API, with the originals built by `prompts.py` itself:

```sh
just examples/walkthrough/onboard-push     # uv run python examples/walkthrough/onboard.py push
```

Look at the import page (the command prints its URL, `/w/walkthrough/import/<id>`): progress messages, 4 agents at 100% fidelity, one disagreement (the allergen never rule: Bayleaf and Juniper against both Copper Woks), duplicates and variables. Pick Bayleaf's wording and **Apply**, then publish in **Instructions → Changes** (`/w/walkthrough/changes`). Or let the script do what the reviewer would:

```sh
just examples/walkthrough/onboard-publish  # resolve the drift, apply, publish release 1
```

Expected: release 1 at `/w/walkthrough/releases`, and each agent's instructions at `/w/walkthrough/agents/<agent>/prompt`, the same text `prompts.py` built (fidelity 100%).

**Or: from an existing sop project.** A team that already keeps its instructions as a sop project (or exports one from wherever its agents' instructions live, a database say) onboards without a coding agent. `onboarding/sops/` is that project for these four agents, with the shared allergen wording already chosen. In **Instructions → Import**, choose **A sop project** and pick that folder (or, with a GitHub repository connected, read it from there), then **Add original** for each agent with the prompt `prompts.py` builds. The import page shows, per agent, the share of the original's sentences kept, how many rules appear word for word, how similar the two texts are, and the lines that are missing: Bayleaf and Juniper keep everything; both Copper Woks lose their own wording of the allergen never rule (9 of 10 rules word for word, 1 line missing), which is the drift the team settled. **Apply as drafts** writes exactly the project's files; publish as usual. The script does all of it:

```sh
just examples/walkthrough/onboard-sop     # instead of onboard: import onboarding/sops/ with the originals, apply, publish release 1
```

`WALKTHROUGH_ONBOARD=sop just examples/walkthrough/all` runs the whole walkthrough this way; the release and everything after it are the same.

### 3. Switch the agent to fetch its instructions

`agent/after/agent.py` is the same agent with its instructions coming from sopops. `prompts.py` is gone; the agent passes `AGENT_NAME` when each call starts, notes the release on the session, streams the call to sopops and posts it at shutdown:

```diff
 from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli
+from sopops.livekit import attach

-from prompts import build_prompt
 from tools import TOOLS
@@
-INSTRUCTIONS = build_prompt(AGENT_NAME)
+# Used only if sopops can't be reached and this machine has never fetched the prompt before.
+FALLBACK = (
+    "You are the phone host for a restaurant. Apologize that the ordering system is down, "
+    "take the caller's name and phone number, and say a staff member will call back shortly."
+)
@@
-    await session.start(room=ctx.room, agent=Agent(instructions=INSTRUCTIONS, tools=TOOLS))
+    # The live release's prompt (or the last good copy on disk), the release noted in session.userdata, and the
+    # call streamed to sopops as it happens, then posted in full when the job ends.
+    ins = await attach(ctx, session, agent=AGENT_NAME, fallback=FALLBACK, stream=True)
+    log.info("sopops: %s release %s (%s)", ins.agent, ins.release, ins.source)
+
+    await session.start(room=ctx.room, agent=Agent(instructions=ins.text, tools=TOOLS))
```

and `sopops[livekit]` in `pyproject.toml`. To run it against a real LiveKit project: make a token with the `runtime` and `ingest` scopes under **the gear icon → Tokens**, fill `agent/after/.env.local` from `.env.example`, then `AGENT_NAME=juniper-grill uv run agent.py dev`. The rest of the walkthrough doesn't need LiveKit: step 7 drives the same adapter with stand-ins for LiveKit's objects.

### 4. Generate the calls

```sh
just examples/walkthrough/generate     # --seed 7 --backfill 200 --new 100
```

Writes `out/backfill.jsonl` (200 calls over the 14 days before today, on release 1's instructions), `out/new.jsonl` (100 calls for after the fix) and `out/ground_truth.json`. Each call is a LiveKit job: its `ChatContext` items (messages, `function_call`, `function_call_output`). What's planted, per rule:

| Rule | Planted failure modes | Verdict it should get |
|---|---|---|
| Allergen check, never 1: `Never say an item is "allergen-free" or "safe"` | tells the caller a dish is safe; claims the kitchen is free of the allergen | real |
| | says it *can't* promise a dish is safe (not a failure) | checker wrong (fake mode only flags it) |
| Allergen check, step 3: check each item with `lookup_allergens` | answers from memory or points to the website | real |
| Allergen check, warning sign 1: anaphylaxis or an EpiPen, transfer | keeps going instead of transferring | real |
| Delivery orders, step 2: `check_delivery_zone` | takes the address without checking | real |
| Delivery orders, never 1: `Never tell the caller a delivery time` | gives the time from the zone check: worded too broadly, the team meant "before the zone check" | acceptable, with a suggested rewording |
| Reservations, never 1: more than 8 guests needs the manager | confirms a large party itself | real |
| Reservations, step 2: an OR step (book: `check_reservations`, OR change: `change_reservation`, OR cancel: `cancel_reservation`) | the caller asks to cancel and the agent moves the booking instead: the wrong option | real |

About half the calls have nothing planted. The new calls have far fewer "safe" claims (the fix in step 6) and are checked against the reworded delivery rule, under which only a time given before the zone check counts.

### 5. Backfill

```sh
just examples/walkthrough/backfill
```

Posts the 200 calls with the SDK (`SopOps.post_session`, 8 at a time), each converted by the LiveKit adapter's own `from_livekit`, stamped release 1, and waits for the pipeline. These pages are in **QA** mode (the switch in the top bar):

- **Sessions** (`/w/walkthrough/sessions`): 200 calls, each with its transcript, tool calls and the result per procedure. Open one with a finding: the rule, its status and the quote that shows it highlighted in the transcript.
- **Insights** (`/w/walkthrough/results`): sessions checked, followed end to end and needing attention; the procedure scoreboard (delivery orders looks worst, because of the broad rule); a procedure's rule ladder (`/w/walkthrough/results/procedures/allergen-check`).
- **Patterns** (`/w/walkthrough/patterns?tab=all`): each rule with findings is a pattern, grouped by theme; a rule with 8 or more findings is split into named failure modes. Expected: allergen never 1 split into three (the two real claims and the disclaimer), the delivery-time rule with 53 findings, and the others as in the table. A pattern's page (`/w/walkthrough/patterns/<id>`) has its rule, examples and sessions.
- **Report** (`/w/walkthrough/report`): the same, read like a hand-written call review.

### 6. Review: verdicts, suggestions, release 2

**QA → Patterns → Needs review** (`/w/walkthrough/patterns`) is the queue: each pattern with example calls and four verdicts (keys `1` to `4`, `j`/`k` to move). Give them, or run `just examples/walkthrough/review-verdicts`, which gives exactly these:

- "Told the caller a dish is safe for their allergy": **Real mistake**, with a suggestion.
- "Gave the caller a delivery time": **Acceptable**, with a suggestion. Its 53 findings are suppressed at once: they stop counting in Insights.
- "Used the word safe while declining to promise it" (fake mode): **Checker is wrong**. Suppressed, and kept as negative calibration labels.
- The other patterns: **Real mistake**, no suggestion asked for.

**Suggestions** (`/w/walkthrough/suggestions`): one edit per suggestion, each compiled with sop, showing how it changes each agent's instructions. The delivery one rewords the rule to "Never tell the caller a delivery time before the delivery zone check confirms the address"; the allergen one adds guidance about what to say when a caller asks whether a dish is safe. **Save as draft** on each (`just examples/walkthrough/review-apply`), or, with a GitHub repository connected under **the gear icon** (Settings), open each as a pull request instead.

**Instructions → Changes** (`/w/walkthrough/changes`) shows the two procedure edits and how each agent's instructions change. Publish them as release 2 (`just examples/walkthrough/review-publish`). `just examples/walkthrough/review` does all three; it also saves the regression cases of two patterns (`…/patterns/<id>/regression`) to `out/regression-*.jsonl`.

### 7. Replay new calls, as if live

```sh
just examples/walkthrough/replay       # --rate 2 calls/s, each call 30x faster than real time
```

Each of the 100 new calls runs through `attach` exactly as `agent/after/agent.py` would: it fetches the live release (2), opens the session, streams each turn and tool call as LiveKit emits it, and posts the full call at shutdown. Watch **Sessions** while it runs: calls appear while they're still going, then get checked, all on release 2.

### 8. Verify

```sh
just examples/walkthrough/verify
```

Expected in fake mode, all PASS:

- 200 backfill and 100 new sessions, all checked; backfill on release 1, new on release 2.
- Findings per rule against the ground truth: recall and precision per rule, for both sets.
- Each planted failure mode matched to a pattern by its calls (names don't matter), and allergen never 1 split into three.
- Verdicts: the acceptable and checker-wrong patterns have all their findings suppressed; the real ones still count. Regression cases exported.
- Both suggestions applied and in release 2.
- **Insights → Release compare** (`/w/walkthrough/results/releases`, 30 days): allergen check's pass rate up from about 53% to about 85%, and its never rule 1 from about 73% to 94%. Delivery orders stays about level: release 1's delivery-time findings are already suppressed by the acceptable verdict, so the rewording shows in the per-rule findings, not in the pass rate.
- Reworded rules keep their history: release 2's delivery never rule 1 continues release 1's (linked by the applied suggestion), with one row in release compare and in the rule ladder ("reworded in release 2"), and one rule pattern holding both releases' findings and the eta verdict.
- Calibration answers (a preview, nothing saved): every procedure has enough verdicts to fit. Try it under **the gear icon → Calibration**: Preview shows the labels used and each threshold before and after. In fake mode the scripted decisions answer every question alike, so every threshold stays where it is; with real models some move.

### 9. Two suggestions on one procedure

```sh
just examples/walkthrough/conflicts
```

Suggestions are written against the procedure as it is, and nothing stops two being written for one procedure before either is applied. This step asks for two more on each of two procedures, applies the first of each pair, and looks at the second:

- **Delivery orders**: guidance for "Took a delivery address without checking the zone", then an exception on the delivery-time rule. They change different lines, so once the first is applied the second says "The procedure changed since this was written. It merges cleanly." **Save as draft** writes both edits.
- **Allergen check**: guidance for "Answered the allergy question without the allergen list", then for "Didn't transfer an allergy emergency". Both add a paragraph at the same place, so the second shows "This conflicts with changes made since it was written", with the procedure's lines now beside its own, and can't be applied. **Rewrite against the current text** writes it again from the file as it is, told to keep the first edit; the old one is dismissed and links to the new one, which applies cleanly.

Expected in fake mode, all PASS: both pairs current before either is applied, `merges` then applied with `merged_into_version`, `conflict` with one conflicting part and apply refused (409), the rewrite current and the old one dismissed, and the final file with both paragraphs. The edits stay as unpublished drafts in **Instructions → Changes**. In real mode the states are printed rather than checked, since a real model's edits may land elsewhere.

### 10. The fixes hold

```sh
just examples/walkthrough/followup
```

Not part of `all`: in real mode it's 60 more calls to check. It publishes step 9's drafts as release 3 (the delivery-zone fix and the allergen-list one), then plays 60 calls through the LiveKit adapter in which those mistakes no longer happen. Ten clean sessions on a procedure after a fix's release make it fixed, so **Mistakes → Fixed** shows "Took a delivery address without checking the zone" fixed in release 3 with no findings in its 27 delivery calls, and Home's "What the fixes did" shows it going from 4% of calls to none. Then sopops writes a fix for the large-party reservations mistake, applied as a draft and left unpublished: the loop mid-turn, as a team would see it on an ordinary day.

## Running it in CI

`tests/test_walkthrough.py` runs `run_all.py --stop --fast` (steps 2 to 9) in fake mode on a free port with its own database: SQLite, or Postgres when `SOPOPS_TEST_DATABASE_URL` is set, like the rest of the suite. Then it serves that same database again with `serve.py --sign-in`, the way `run_all.py --sign-in` ends, and checks that the calls and both releases are still there, that the seeded account signs in, and that a stranger can sign up with no invite.
