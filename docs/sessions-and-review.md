# Sessions, review and models

## Sessions and checks

A session is one call or chat, in one JSON format ([API.md](../API.md#sessions)). Send it with `POST /v1/workspaces/{ws}/sessions` and an `ingest` token, or stream it (`/v1/workspaces/{ws}/sessions/open`, `/v1/workspaces/{ws}/events`, `/v1/workspaces/{ws}/close`). A stream idle for `SOPOPS_SESSION_IDLE_MINUTES` closes as incomplete. Resending is safe: sessions are keyed by platform and session id. Backfill ([example](../examples/import-sessions)):

```sh
sopops import sessions.jsonl --workspace prod
```

The **Sessions** page lists them live, with transcript, tool calls and per-procedure result, quotes highlighted. Each new or changed session runs:

1. **Normalize** turns and tool calls.
2. **Route**: which procedures in its release applied, and which ended early.
3. **Check**: a decision per rule, the reviewer model on flagged ones, a status per rule with quotes, the goal, required tool steps.
4. **Result** per procedure and session: deviated, partial, followed or not measurable ([how](../API.md#sop-checks)).
5. **Patterns**: findings join their rule's pattern ([below](#patterns-review-and-suggestions)).

This runs inside sopops by default. With `HATCHET_CLIENT_TOKEN` it runs on [Hatchet](https://hatchet.run) in `sopops worker`: one run per session version, fair share per workspace, per-model rate limits, a daily recluster. `docker compose --profile hatchet up` starts hatchet-lite ([docker-compose.yml](../docker-compose.yml)). Live UI updates work from any process on Postgres, only from the sopops process on SQLite.

**Insights** compares sessions checked, followed and needing attention against the prior window. It has a trend with release markers, a scoreboard per procedure with its rule ladder and rule pages, and **Release compare** (two releases rule by rule, 95% intervals). Every number links to its sessions ([definitions](../API.md#insights)). The **Report** reads like a hand-written call review.

## Patterns, review and suggestions

**Patterns.** Each rule with findings is a pattern, named by the reviewer model ("Didn't read the order back") or by the rule's words without one. At 8 findings it splits into named failure modes ("Told the caller a dish is safe") that new findings join. Themes group them by call phase. Names and ids stay stable across reclusters ([how](../API.md#patterns-and-themes)).

**Review.** QA → Patterns → Needs review queues patterns for a verdict (keys `1` to `4`):

| Verdict | Means | Then |
|---|---|---|
| Real mistake | Agent was wrong | Findings count. Suggests an instructions edit. |
| Acceptable | Fine here | Findings suppressed: kept, but out of every Insights number. Suggests a rule exception. |
| SOP is wrong | Instruction outdated | Suggests a rule rewrite. |
| Checker is wrong | sopops misjudged | Findings suppressed, kept as calibration labels. |

You can mark one finding wrong. A judged pattern exports its sessions as regression cases (JSON Lines).

States: **needs review** (no verdict, or a recluster changed it), **open** (real mistake or wrong SOP, unfixed), **fixing** (fix applied or PR open or merged; waits for the release to go live with 10 sessions), **fixed**, **regressed** (back on sessions that ran the fix; requeued), **closed** (acceptable or checker wrong). A merged PR moves the pattern on. A closed one reopens it.

**Reworded rules.** sopc gives changed rule text a new id, and every applied suggestion rewords one. On publish, sopops links each reworded rule to the one it replaced: by the suggestion that made the edit, else by wording at the same position, then anywhere in the procedure. The rule keeps its pattern, verdicts, state and history. Sessions keep the wording they ran. Insights shows "reworded in release 4" with earlier wordings on hover. Link or unlink a wrong match by hand on the rule's Insights page ([API.md](../API.md#rule-lineage)).

**Suggestions.** The reviewer proposes the smallest edit that settles the verdict. sopops compiles it with sopc (one retry) and shows how each agent's instructions change. Apply saves a draft, or opens a PR if a GitHub repository is connected. If the procedure changed since, edits on different lines merge if the result compiles. Overlaps show the conflict and block applying. **Rewrite against the current text** redoes it on the current file, keeping later edits ([details](../API.md#review-and-suggestions)).

## Models

Each model call is a typed [DSPy](https://dspy.ai) signature ([`signatures.py`](../src/sopops_server/ai/signatures.py)). Results record the model and instructions version. Bring your own keys.

- **`SOPOPS_MODEL_FAST`** decides: routing, rule checks, pattern assignment.
- **`SOPOPS_MODEL_REASONING`** reviews flagged or uncertain rules, splits rules into checks, names and clusters patterns and themes, and writes suggestions.

Use strings like `anthropic/claude-haiku-4-5` or `anthropic/claude-sonnet-5-5`, with the provider's usual key variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...). One model can fill both roles. Any DSPy provider works, or an OpenAI-compatible endpoint (`SOPOPS_LLM_API_BASE`) for local models. With neither set, sessions are stored unchecked.

**Jev.** Optional. With `pip install 'sopops-server[jev]'` and `TYPESAFE_API_KEY`, TypeSafe's [Jev](https://docs.typesafe.ai) replaces the fast model for decisions: calibrated probabilities, faster and much cheaper. The reviewer still reviews.

**Check modes.** `decide` (default) sends flagged decisions to the reviewer. `review` has it judge every rule. `SOPOPS_CHECK_REVIEW=off` lets decisions stand.

**Spend.** Each call records tokens and estimated cost by purpose and session (prices: `SOPOPS_MODEL_PRICES`). See QA → Insights → Model spend and each session page ([API.md](../API.md#model-spend)). Text sent to models isn't stored.

**Calibration.** Fits decision thresholds per workspace from labeled sessions (verdicts and overrides; checker-wrong labels are negative). Defaults apply until then; `SOPOPS_DECISIONS` overrides them.

```sh
sopops calibrate --workspace WS [--procedure P] [--dry-run]   # or POST /v1/workspaces/{ws}/calibrate
```

It reports each threshold before and after. A procedure needs at least 5 labeled sessions and a rule with 3 labeled failures. It only widens the review band.
