# Sessions, review and models

## Sessions and checks

A session is one call or chat, in one JSON format ([API reference](api/sessions.md)).

- **Post it** to `/v1/workspaces/{ws}/sessions` with an `ingest` token, or stream it to `/v1/workspaces/{ws}/sessions/open`, `/v1/workspaces/{ws}/events` and `/v1/workspaces/{ws}/close`.
- **A stream** idle for `SOPOPS_SESSION_IDLE_MINUTES` closes as incomplete.
- **Resending is safe.** Sessions are keyed by platform and session id.
- **Past sessions** come in from a file ([example](../examples/import-sessions)):

```sh
sopops import sessions.jsonl --workspace prod
```

The **Sessions** page lists sessions as they arrive, each with its transcript, tool calls and result per procedure, with quotes highlighted. Each new or changed session goes through these steps:

1. **Normalize** turns and tool calls.
2. **Route**: which procedures in its release applied, and which ended early.
3. **Check**: a decision per rule, the reviewer model on flagged ones, a status per rule with quotes, the goal, required tool steps.
4. **Result** per procedure and session: deviated, partial, followed or not measurable ([how](api/checks-and-review.md#sop-checks)).
5. **Patterns**: findings join their rule's pattern ([below](#patterns-review-and-suggestions)).

This runs inside sopops by default. With `HATCHET_CLIENT_TOKEN` it runs on [Hatchet](https://hatchet.run) in `sopops worker`: one run per session version, fair share per workspace, per-model rate limits, a daily recluster. `docker compose --profile hatchet up` starts hatchet-lite ([docker-compose.yml](../docker-compose.yml)).

Live UI updates work from any process on Postgres, only from the sopops process on SQLite.

**Insights** compares sessions checked, followed and needing attention against the same length of time just before ([definitions](api/insights.md)). Every number links to its sessions.

- A trend with release markers.
- A scoreboard per procedure, with its rule ladder and rule pages.
- **Release compare**: two releases rule by rule, with 95% intervals.
- **Report**, which reads like a hand-written call review.

## Patterns, review and suggestions

**Patterns.** Each rule with findings is a pattern, named by the reviewer model ("Didn't read the order back") or by the rule's words without one. At 8 findings it splits into named failure modes ("Told the caller a dish is safe") that new findings join. Themes group them by call phase. Names and ids stay stable across reclusters ([how](api/checks-and-review.md#patterns-and-themes)).

**Review.** Review → Mistakes → Needs review queues patterns for a verdict (keys `1` to `4`):

| Verdict | Means | Then |
|---|---|---|
| Real mistake | Agent was wrong | Findings count. Suggests an instructions edit. |
| Acceptable | Fine here | Findings suppressed: kept, but out of every Insights number. Suggests a rule exception. |
| SOP is wrong | Instruction outdated | Suggests a rule rewrite. |
| Checker is wrong | sopops misjudged; the agent didn't make this mistake | Findings suppressed, kept as calibration labels, and listed on Fine tune. |

You can mark one finding wrong. A judged pattern exports its sessions as regression cases (JSON Lines).

**Corrections.** On a session, any rule can get its right status:

| Control | On | What it does |
|---|---|---|
| Wrong? | A finding | Right or wrong, then "What actually happened?" with the rule's statuses (you can skip it). A wrong finding is suppressed. |
| Wrong status | Any rule | Pick the status it should have. On a finding, a status that isn't a finding suppresses it, like Wrong?. |
| Checker missed this | A done step or an ok never rule | Pick the failure the checker missed (skipped, out of order, violated). |

A corrected rule shows the status, who gave it and when. A missed mistake doesn't add a finding: the session's result, its patterns and Insights keep what the checker found, so a correction never changes a number without showing it. Every correction is a calibration label. The latest call on a rule wins, and it outlives a re-check.

**Fine tune.** Review → Fine tune collects data for tuning sopops to your calls; its first collection point is **Checker spot checks**, one table with the patterns marked Checker is wrong on Mistakes and spot checks. Spot checks ask about rules the checker passed, one batch at a time (25 by default, `PUT /v1/workspaces/{ws}/spot-checks/settings`), so the work doesn't grow with call volume. A batch is spread across procedures and agents, and takes close calls and decisions the reviewer model changed first. Items are grouped by call, so one transcript answers all its rules, with filters for agent, procedure, rule, dates and why it was picked. Keys: `1` says the checker was right, `2` onward pick the right status, `j` and `k` move. A finished batch offers "Give me N more"; nothing is drawn twice, and an item left unanswered for 7 days expires quietly. Answers are labels, like corrections.

| State | When |
|---|---|
| needs review | No verdict, or a recluster changed it. |
| open | Real mistake or wrong SOP, unfixed. |
| fixing | Fix applied, or a pull request open or merged. Waits for the release to go live with 10 sessions. |
| fixed | No findings in 10 sessions on that release or later. |
| regressed | A finding came back on sessions that ran the fix. Requeued. |
| closed | Acceptable, or checker wrong. |

A merged pull request moves the pattern on. A closed one reopens it.

**Reworded rules.** sopc gives changed rule text a new id, and every applied suggestion rewords one. On publish, sopops links each reworded rule to the one it replaced: by the suggestion that made the edit, else by wording at the same position, then anywhere in the procedure ([API reference](api/checks-and-review.md#rule-lineage)).

- The rule keeps its pattern, verdicts, state and history.
- Sessions keep the wording they ran.
- Insights shows "reworded in release 4", with earlier wordings on hover.
- Link or unlink a wrong match by hand on the rule's Insights page.

**Suggestions.** The reviewer proposes the smallest edit that settles the verdict. sopops compiles it with sopc (one retry) and shows how each agent's instructions change ([details](api/checks-and-review.md#review-and-suggestions)).

- Apply saves a draft, or opens a pull request if a GitHub repository is connected.
- If the procedure changed since, edits on different lines merge when the result compiles.
- Overlaps show the conflict and block applying.
- **Rewrite against the current text** redoes the edit on the current file, keeping later changes.

## Models

Each model call is a typed [DSPy](https://dspy.ai) signature ([`signatures.py`](../src/sopops_server/ai/signatures.py)). Each result records its model and instructions version. You bring your own keys.

| Role | What it does |
|---|---|
| `SOPOPS_MODEL_FAST` | Decides: routing, rule checks, pattern assignment. |
| `SOPOPS_MODEL_REASONING` | Reviews flagged or uncertain rules, splits rules into checks, names and clusters patterns and themes, writes suggestions. |

Use strings like `anthropic/claude-haiku-4-5` or `anthropic/claude-sonnet-5-5`, with the provider's usual key variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...). One model can fill both roles. Any DSPy provider works, or an OpenAI-compatible endpoint (`SOPOPS_LLM_API_BASE`) for local models. With neither role set, sessions are stored unchecked.

| Topic | What to know |
|---|---|
| **Jev** | With `pip install 'sopops-server[jev]'` and a TypeSafe key, TypeSafe's [Jev](https://docs.typesafe.ai) answers decisions instead of the fast model: calibrated probabilities, faster and much cheaper. The reviewer still reviews. Setup asks for a key. A deployment with a model gateway can serve Jev (or another system-1 classifier) from there instead (`SOPOPS_SYSTEM1_URL`), keeping the key in the gateway. |
| **A team's own keys** | The environment variables are the deployment's defaults. In setup, and later in Settings under Checks, a team adds its own TypeSafe key, picks Anthropic, OpenAI, Google or any other LiteLLM provider, pastes a key (or uses the deployment's) and picks the two models from those the key can use. Each key and model is tried before it's saved, stored encrypted with `SOPOPS_SECRET_KEY`, and never shown again. Anything a team doesn't set uses the default. |
| **Check modes** | `decide` (default) sends flagged decisions to the reviewer. `review` has it judge every rule. `SOPOPS_CHECK_REVIEW=off` lets decisions stand. |
| **Tracing** | An extension can register a listener (`sopops_server.ai.tracing.add_listener(after, before)`) that sees each model call, decisions included: workspace, session, procedure and rules, purpose, signature, inputs, outputs and reasoning, model, instructions version, tokens, latency, any error, and a run id shared by one session's check. `before` can add request headers to calls to a hosted model gateway (`sopops/...` models) only. The web app and `sopops worker` both load extensions. None is registered by default, and a failing one never stops a check. |
| **Spend** | Each call records tokens and estimated cost by purpose and session (prices: `SOPOPS_MODEL_PRICES`). See Results → Insights → Model spend, and each session page ([API reference](api/insights.md#model-spend)). Text sent to models isn't stored. |

**Calibration.** sopops fits decision thresholds per workspace from labeled sessions (verdicts, overrides, corrections and spot checks; checker-wrong labels are negative, missed mistakes count as failures). Defaults apply until then; `SOPOPS_DECISIONS` overrides them.

```sh
sopops calibrate --workspace WS [--procedure P] [--dry-run]   # or POST /v1/workspaces/{ws}/calibrate
```

It reports each threshold before and after. A procedure needs at least 5 labeled sessions and a rule with 3 labeled failures. It only widens the review band.
