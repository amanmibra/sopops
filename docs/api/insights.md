# Insights

Pass rates, trends, release comparisons and A/B tests, plus what sopops spent on model calls. Every number comes from one module (`sopops_server.insights`) with one set of definitions, computed on the fly.

## Definitions

| Term | Definition |
|---|---|
| Window | `[start, end]` on the session's start time, both ends inclusive, at most 366 days. `window=24h\|7d\|30d\|90d\|365d` (default 7d), or `start` and `end`. The prior window is the same length, right before it. |
| Eligible session | Status `checked` or `incomplete`, with a result. In-progress and failed sessions are counted, never rated. Excluded sessions are counted separately. |
| Session outcome | The worst result of its procedure checks in scope (only the `procedure` filter's, if set). **Followed end to end**: followed. **Needs attention**: deviated. |
| Pass rate (procedure) | Checks followed / measurable checks (deviated, partial or followed). `not_measurable` checks are never rated. |
| Pass rate (rule) | Steps: done / (done + skipped + out of order). Never rules: ok / (ok + violated). `n` is that denominator. `not_reached` and `not_applicable` are shown as a share, never counted. Warning signs are never in a pass rate: they are present (`present` or `unheeded`) or absent. |
| Percentages | Whole numbers, rounded half up, with their `n`; null (shown "—") when the denominator is 0. Rate deltas are in points; count deltas in %. |
| Suppressed findings | A verdict or override says they aren't a problem ([Review](checks-and-review.md#review-and-suggestions)). They're left out of every count and rate, with procedure and session results rolled up without them, and counted on their own as `suppressed`. |
| Rules | Counted by lineage ([Rule lineage](checks-and-review.md#rule-lineage)): a reworded rule's results add up with its earlier wordings', under the wording sessions ran most recently (`rule_id`, `text`). |
| Versions | Sessions with the same agent, release and `config_hash` ([A/B tests](../ab-tests.md)), labeled A, B, C by their first session ever (in the test or agent, when set). |
| Comparisons | A 95% Wilson interval per rate. A side with n < 10 is `thin` and never flagged. `regressed` and `improved` need a change of 5 points or more and intervals that don't overlap. Releases are compared by the release each session ran, never by date. |

Rule rows carry their lineage:

| Field | Type | |
|---|---|---|
| `lineage_id` | string | The id of the rule's first wording. |
| `wording_history` | `[{rule_id, text, from_release}]` | Oldest first. `from_release` is the release that published the wording, else the first a session ran it on. |
| `reworded_in` | int | The release that brought the current wording; null if never reworded. |

## Endpoints

All take the window filters plus `agent`, `procedure`, `release` (repeatable), `channel` and `test` (an A/B test's sessions).

| Endpoint | What it returns |
|---|---|
| `GET /v1/workspaces/{ws}/insights/overview` | Tiles (current, prior, deltas): sessions checked, followed end to end, needs attention, excluded, procedure pass rate, goal rate. A trend per day (windows up to 31 days) or week, with release markers. A procedure scoreboard: checks, pass rate and delta, top failing rules, never violations, open patterns, excluded. |
| `GET /v1/workspaces/{ws}/insights/procedures/{id}` | Tiles and trend; the rule ladder (per rule: n, pass, fail and skip rates, not-counted share, warning-sign presence, findings, patterns); the release split (previous against latest release in the window, per rule); warning-sign impact (pass rate with the sign present against absent); and patterns by theme. |
| `GET /v1/workspaces/{ws}/insights/procedures/{id}/rules/{rule_id}` | The rule across its wordings (`rule_id` is any of them): `wordings` (each with `rule_id`, `from_release`, first and last seen, results), `reworded_in`, its pass rate overall, per release and per agent (with intervals), its patterns, and the 5 most recent findings with their quotes. |
| `GET /v1/workspaces/{ws}/insights/releases/compare?a=&b=` | Release B against A. Fields below. |
| `GET /v1/workspaces/{ws}/insights/versions?v=` | A/B tests. Fields below. |
| `GET /v1/workspaces/{ws}/insights/report` | The review report: headline numbers (plus sessions with problems on more than one rule), then per procedure its themes, each with its patterns: the rule they're about, failure modes, example phrasings and the sessions. |

```sh
curl -s -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/insights/overview?window=30d"
```

```json
{
  "filters": {"start": "2026-09-22T02:49:19Z", "end": "2026-10-22T02:49:19Z", "agent": null, "procedure": null, "releases": [], "channel": null, "test": null},
  "thresholds": {"min_n": 10, "flag_points": 5, "warning_drop_points": 10, "hot_min_n": 15, "fail_warn": 8, "fail_bad": 15, "agent_min_n": 3, "rising_min": 5},
  "tiles": {
    "current": {"sessions_checked": 111, "followed": 56, "need_attention": 22, "partial": 33, "not_measurable": 0,
                "excluded": 26, "followed_pct": 50, "need_attention_pct": 20, "checks": 111, "measurable_checks": 111,
                "pass_rate": 50, "goal_n": 107, "goal_rate": 47, "rule_n": 418, "rule_passes": 390, "rule_pass_rate": 93,
                "never_violations": 19, "never_sessions": 18, "suppressed": 0},
    "prior": {"sessions_checked": 0, "pass_rate": null, "goal_rate": null},
    "delta_points": {"followed_pct": null, "pass_rate": null, "goal_rate": null},
    "delta_pct": {"sessions_checked": null, "followed": null, "need_attention": null}
  },
  "trend": {"bucket": "day", "buckets": [{"start": "2026-10-19T00:00:00Z", "end": "2026-10-20T00:00:00Z", "followed": 12, "partial": 7, "deviated": 4, "not_measurable": 0, "pass_rate": 52, "n": 23}],
            "releases": [{"release": 4, "first_session": "2026-10-19T16:55:02Z"}]}
}
```

**Release compare.** The default is the latest release with sessions in the window, against the one before. Per procedure and per rule (by lineage) it returns both rates with n and intervals, the delta in points (null under the minimum n) and a flag.

```json
{
  "a": 2,
  "b": 3,
  "procedures": [{"procedure_id": "allergen-check", "a": {"n": 4, "pass_rate": 0, "interval": [0, 49]}, "b": {"n": 8, "pass_rate": 25, "interval": [7, 59]}, "delta_points": null, "flag": "thin"}],
  "rules": [{
    "procedure_id": "allergen-check", "rule_id": "ccc0048aa321", "lineage_id": "ccc0048aa321", "kind": "step", "index": 1,
    "text": "Ask who the allergy is for and what it is", "a_text": null, "rule_change": null, "reworded_in": null,
    "a": {"n": 4, "passes": 3, "fails": 1, "pass_rate": 75, "interval": [30, 95], "thin": true, "counts": {"done": 3, "skipped": 1}},
    "b": {"n": 8, "passes": 8, "fails": 0, "pass_rate": 100, "interval": [68, 100], "thin": true, "counts": {"done": 8}},
    "delta_points": null, "raw_delta_points": 25, "flag": "thin"
  }],
  "sessions_a": 11,
  "sessions_b": 43
}
```

A reworded rule has `rule_change: "changed"`, with A's wording in `a_text`, the release that brought B's wording in `reworded_in`, and every wording in `wording_history`. A rule only in B is `new`, and one only in A is `removed`.

**Versions (A/B tests)** returns:

- every version in the window: `id`, `label`, `agent`, `release`, `config`, the prompt text if one was reported, `sessions`, `checked`, `first_at` and `last_at`;
- the `tests` with sessions in the window, and `untagged` (sessions with no config);
- up to 5 versions compared. `v` is repeatable, with the baseline first; the default is the most-run, oldest first.
- the comparison itself: `differs` (what isn't the same across them), then per procedure (triggered, pass rate, goal rate) and per rule (pass rate, or presence for warning signs), one cell per version with its delta and flag against the baseline.

## Model spend

Every model call sopops makes is recorded in `model_calls`: the model, the input and output tokens it reported, and its estimated cost. Each call is recorded for its workspace, the session it was about, and its purpose.

| Purpose | |
|---|---|
| `route` | Deciding which procedures applied. |
| `check` | Checking rules. |
| `review` | The reasoning model's second look. |
| `cluster` | Assigning findings, splitting and naming patterns. |
| `theme` | Grouping patterns by call phase. |
| `suggestion` | Writing a suggested edit. |
| `calibrate`, `onboarding` | Fitting thresholds, and imports. |

- The text sent to models and their replies aren't stored.
- Calls are seen through DSPy's usage tracker, so every client path counts: a `dspy.LM`, native or through LiteLLM, and Jev.
- A cached answer costs nothing and isn't recorded.
- Costs come from a price table in USD per million tokens (input, output): Jev at $0.042 input and free output, and the current Claude models at list prices.
- `SOPOPS_MODEL_PRICES` (JSON, `{"model": [input, output]}`) changes or adds entries. A model string matches its name with or without the provider, else the longest entry it starts with (dated snapshots). A model with no price has its tokens recorded and a null cost.

| Endpoint | What it returns |
|---|---|
| `GET /v1/workspaces/{ws}/usage?window=` | Over the window (`window`, or `start` and `end`; by when the call was made): `total`, `by_purpose` and `by_model` (largest cost first), `sessions` and `per_session_usd` (the cost of calls about a session, per session), and `daily`, one row per UTC day. |
| `GET /v1/model-prices?workspace=` | The price table (`source`: `default` or `env`), and each configured role's model with its price (the team's own models, given `workspace`). |

```sh
curl -s -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/usage?window=30d"
```

```json
{
  "start": "2026-09-22T02:49:40Z",
  "end": "2026-10-22T02:49:40Z",
  "total": {"calls": 489, "input_tokens": 976918, "output_tokens": 81533, "cost_usd": 1.32936, "unpriced_calls": 0},
  "by_purpose": [
    {"purpose": "review", "calls": 96, "input_tokens": 375536, "output_tokens": 30200, "cost_usd": 1.053072, "unpriced_calls": 0},
    {"purpose": "check", "calls": 224, "input_tokens": 325780, "output_tokens": 31334, "cost_usd": 0.082839, "unpriced_calls": 0}
  ],
  "by_model": [
    {"model": "anthropic/claude-sonnet-5-5", "calls": 147, "cost_usd": 1.307758, "unpriced_calls": 0},
    {"model": "jev-1.13.0", "calls": 342, "cost_usd": 0.021602, "unpriced_calls": 0}
  ],
  "sessions": 131,
  "per_session_usd": 0.009199,
  "daily": [{"day": "2026-10-19", "calls": 318, "input_tokens": 612004, "output_tokens": 52210, "cost_usd": 0.91, "unpriced_calls": 0}]
}
```

A session's detail has its own `model_calls` and `cost_usd`. To estimate what a team's own models would cost before picking them, see [`GET /ai/estimate`](teams-and-access.md#ai-keys-and-models).
