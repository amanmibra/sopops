# Sessions, review and models

## Sessions and checks

A session is one conversation: a voice call or a text chat. Every source converts into one canonical JSON format ([API.md](../API.md#sessions)), sent to `POST …/sessions` with an `ingest` token, or streamed while it runs (`…/sessions/open`, `…/events`, `…/close`; a session idle for `SOPOPS_SESSION_IDLE_MINUTES` closes as incomplete). Sending the same session again is safe: it's keyed by platform and session id. `sopops import sessions.jsonl --workspace prod` reads a backfill straight into the database ([examples/import-sessions](../examples/import-sessions)).

The **Sessions** page lists them live, each with its transcript, tool calls, and how it went against each procedure, with the quotes highlighted. Each new or changed session runs the pipeline:

1. **normalize** the turns and tool calls;
2. **route**: which of the agent's procedures, in the release it ran on, applied, and which ended before they started or finished;
3. **check** each procedure that applied: a decision per rule, the reviewer model on what they flag, a status per rule with quotes, the goal; required tool steps against the tool calls;
4. a **result** per procedure and per session: deviated, partial, followed or not measurable ([how](../API.md#sop-checks));
5. **patterns**: each finding joins its rule's pattern ([below](sessions-and-review.md#patterns-review-and-suggestions)).

Without Hatchet it runs inside sopops, after the response. With `HATCHET_CLIENT_TOKEN` it runs on [Hatchet](https://hatchet.run) in `sopops worker`: one run per session version, a fair share per workspace, model calls rate-limited per model, and a daily recluster. `docker compose --profile hatchet up` runs hatchet-lite ([docker-compose.yml](../docker-compose.yml)). Live updates reach the UI from any process on Postgres (LISTEN/NOTIFY); on SQLite, from the sopops process only.

**Insights** shows sessions checked, followed end to end and needing attention over a window, against the prior one; a trend with release markers; a scoreboard per procedure, its rule ladder and each rule's page; and **Release compare**, two releases side by side, rule by rule, with 95% intervals. Every number links to its sessions ([definitions](../API.md#insights)). The **Report** reads like a hand-written call review.

## Patterns, review and suggestions

**Patterns.** Every rule with findings is a pattern, named by the reviewer model for what the agent did wrong ("Didn't read the order back"; without a model, the rule's own words). Once a rule has 8, the reviewer model splits it into named failure modes ("Told the caller a dish is safe"), new findings are assigned to them, and themes group them by the phase of the call. Reclustering keeps names and ids stable ([how](../API.md#patterns-and-themes)).

**Review.** QA → Patterns → Needs review is a queue (keys `1` to `4`) of patterns waiting for a verdict:

| Verdict | Means | Then |
|---|---|---|
| Real mistake | The agent did something wrong. | Findings keep counting; a suggested edit to the instructions. |
| Acceptable | Fine in this situation. | Findings suppressed: kept, but out of every Insights number; a suggested exception to the rule. |
| SOP is wrong | The instruction is outdated. | A suggested rewrite of the rule. |
| Checker is wrong | sopops misjudged. | Findings suppressed, and kept as calibration labels. |

A single finding can be marked wrong too. A pattern with a verdict exports its sessions as regression cases (JSON Lines).

**Reworded rules.** sopc gives a rule a new id when its text changes, and every applied suggestion rewords one. sopops records which rule each reworded rule replaced when you publish (from the suggestion that made the edit, else by matching the wording at the same position, then anywhere in the procedure), so the rule keeps its pattern, verdicts, review state and Insights history across wordings, while each session keeps the exact wording it ran. Insights marks a reworded rule ("reworded in release 4", the earlier wordings on hover). When the matcher is wrong, link or unlink the two rules by hand on the rule's Insights page ([API.md](../API.md#rule-lineage)).

Each pattern then moves through review states: **needs review** (no verdict, or a recluster changed its findings since), **open** (a real mistake or a wrong SOP, not fixed yet), **fixing** (a suggestion applied or its pull request open or merged, waiting for the release to go live and for 10 sessions on it), **fixed**, or **regressed** (it came back on sessions that ran the fix, so it's back in the queue), and **closed** for acceptable and checker-wrong. Pull requests are followed on GitHub: merged moves the pattern on, closed reopens it.

**Suggestions.** The reviewer model proposes the smallest edit to the procedure that settles the verdict; sopops compiles it with sopc (one retry if it doesn't) and shows how each agent's instructions change. Apply saves it as a draft, or opens a pull request when the workspace has a GitHub repository connected. When the procedure changed after a suggestion was written (another one was applied, or someone edited it), the edit is merged into the file as it is now if the two touch different lines and the result compiles; if they overlap, the suggestion shows the conflicting lines and can't be applied, and **Rewrite against the current text** writes it again from the file as it is, keeping the edits made since. Details: [API.md](../API.md#review-and-suggestions).

## Models

Every model call is a typed [DSPy](https://dspy.ai) signature ([`src/sopops/ai/signatures.py`](../src/sopops/ai/signatures.py)), and every result records the model and instructions version that made it. Bring your own keys.

- **Two roles.** `SOPOPS_MODEL_FAST` answers the decisions (routing, rule checks, pattern assignment) and `SOPOPS_MODEL_REASONING` is the reviewer: it judges the rules the decisions flag or aren't sure about, splits rules into checks, names patterns, clusters, names themes and writes suggestions. Model strings like `anthropic/claude-haiku-4-5` and `anthropic/claude-sonnet-5-5`, with the provider's key in its usual variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...); either alone serves both. Any provider DSPy knows works, or an OpenAI-compatible endpoint for local models (`SOPOPS_LLM_API_BASE`). Unset, sessions are stored but not checked.
- **Jev (optional).** With `pip install 'sopops[jev]'` and a `TYPESAFE_API_KEY`, TypeSafe's [Jev](https://docs.typesafe.ai) answers the decisions instead of the fast model: calibrated probabilities, faster and much cheaper than an LLM call. The reviewer still reviews.
- **Check modes.** `decide` (default): decisions first, the reviewer on what they flag. `review`: the reviewer judges every rule. `SOPOPS_CHECK_REVIEW=off` lets the decisions stand.
- **Spend.** Every model call is recorded with its tokens and an estimated cost (a price table you can override with `SOPOPS_MODEL_PRICES`), by purpose and session: QA → Insights → Model spend, and each session's page ([API.md](../API.md#model-spend)). The text sent to models isn't stored.
- **Calibration.** Decision thresholds can be fitted to a workspace's own labeled sessions (verdicts and overrides are the labels; checker-wrong ones are negative) and are stored per workspace; until then the defaults apply (`SOPOPS_DECISIONS` overrides them). Run it with `sopops calibrate --workspace WS [--procedure P] [--dry-run]` or `POST …/calibrate`: it reports each threshold before and after, and refuses a procedure with fewer than 5 labeled sessions or no rule with 3 labeled failures. Calibration widens the review band, never narrows it.
