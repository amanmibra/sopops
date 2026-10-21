# Insights

Pass rates, trends, release comparisons and A/B tests, plus what sopops spent on model calls. Every number comes from one module (`sopops.insights`) with one set of definitions, computed on the fly.

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
| Rules | Counted by lineage ([Rule lineage](checks-and-review.md#rule-lineage)): a reworded rule's results add up with its earlier wordings', under the wording sessions ran most recently (`rule_id`, `text`). See below. |
| Versions | Sessions with the same agent, release and `config_hash` ([A/B tests](../ab-tests.md)), labeled A, B, C by their first session ever (in the test or agent, when set). |
| Comparisons | A 95% Wilson interval per rate. A side with n < 10 is `thin` and never flagged. `regressed` and `improved` need a change of 5 points or more and intervals that don't overlap. Releases are compared by the release each session ran, never by date. |

**Rule rows** have `lineage_id`, `wording_history` (`[{rule_id, text, from_release}]`, oldest first; `from_release` is the release that published the wording, else the first a session ran it on) and `reworded_in` (the release that brought the current wording, null if never reworded).

## Endpoints

All take the window filters plus `agent`, `procedure`, `release` (repeatable), `channel` and `test` (an A/B test's sessions).

| Endpoint | What it returns |
|---|---|
| `GET /v1/workspaces/{ws}/insights/overview` | Tiles (current, prior, deltas): sessions checked, followed end to end, needs attention, excluded, procedure pass rate, goal rate. A trend per day (windows up to 31 days) or week, with release markers. A procedure scoreboard: checks, pass rate and delta, top failing rules, never violations, open patterns, excluded. |
| `GET /v1/workspaces/{ws}/insights/procedures/{id}` | Tiles and trend; the rule ladder (per rule: n, pass, fail and skip rates, not-counted share, warning-sign presence, findings, patterns); the release split (previous vs latest release in the window, per rule); warning-sign impact (pass rate with the sign present vs absent); and patterns by theme. |
| `GET /v1/workspaces/{ws}/insights/procedures/{id}/rules/{rule_id}` | The rule across its wordings (`rule_id` is any of them): `wordings` (each with `rule_id`, `from_release`, first and last seen, results), `reworded_in`, its pass rate overall, per release and per agent (with intervals), its patterns, and the 5 most recent findings with their quotes. |
| `GET /v1/workspaces/{ws}/insights/releases/compare?a=&b=` | Release B against A. See below. |
| `GET /v1/workspaces/{ws}/insights/versions?v=` | A/B tests. See below. |
| `GET /v1/workspaces/{ws}/insights/report` | The review report: headline numbers (plus sessions with problems on more than one rule), then per procedure its themes, each with its patterns: the rule they're about, failure modes, example phrasings and the sessions. |

**Release compare.** The default is the latest release with sessions in the window, against the one before. It returns, per procedure and per rule (by lineage):
- both rates with n and intervals;
- the delta in points (null under the minimum n) and a flag;
- whether the rule is new, removed or reworded: `rule_change: changed`, with A's wording in `a_text`, the release that brought B's wording in `reworded_in`, and every wording in `wording_history`.

**Versions (A/B tests)** returns:
- every version in the window: `id`, `label`, agent, release, `config`, the prompt text if one was reported, sessions, checked, first and last call;
- the tests with sessions in the window, and `untagged` (sessions with no config);
- up to 5 versions compared. `v` is repeatable, with the baseline first; the default is the most-run, oldest first. The comparison has `differs` (what isn't the same across them), then per procedure (triggered, pass rate, goal rate) and per rule (pass rate, or presence for warning signs), one cell per version with its delta and flag against the baseline.

## Model spend

Every model call sopops makes is recorded in `model_calls`: the model, the input and output tokens it reported, and its estimated cost. Each call is recorded for its workspace, the session it was about (routing, checks, the reviewer, assigning its findings) and its purpose: `route`, `check`, `review`, `cluster` (assigning findings, splitting and naming patterns), `theme`, `suggestion`, `calibrate` or `onboarding`.

The text sent to models and their replies aren't stored. Calls are seen through DSPy's usage tracker, so every client path counts (a `dspy.LM`, native or through LiteLLM, and Jev). A cached answer costs nothing and isn't recorded.

**Prices.** Costs come from a price table in USD per million tokens (input, output): Jev at $0.042 input and free output, and the current Claude models at list prices. `SOPOPS_MODEL_PRICES` (JSON, `{"model": [input, output]}`) changes or adds entries. A model string matches its name with or without the provider, else the longest entry it starts with (dated snapshots). A model with no price has its tokens recorded and a null cost.

| Endpoint | What it returns |
|---|---|
| `GET /v1/workspaces/{ws}/usage?window=` | Over the window (`window`, or `start` and `end`; by when the call was made): `total` (`calls`, `input_tokens`, `output_tokens`, `cost_usd`, `unpriced_calls`), `by_purpose` and `by_model` (largest cost first), `sessions` and `per_session_usd` (the cost of calls about a session, per session), and `daily`, one row per UTC day. |
| `GET /v1/model-prices?workspace=` | The price table (`source`: `default` or `env`), and each configured role's model with its price (the team's own models, given `workspace`). |

A session's detail has its own `model_calls` and `cost_usd`. To estimate what a team's own models would cost before picking them, see [`GET /ai/estimate`](teams-and-access.md#ai-keys-and-models).
