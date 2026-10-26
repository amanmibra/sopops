# Checks and review

How sopops checks each session against its procedures, groups the same mistake across sessions, turns a reviewer's verdict into a suggested fix, and follows a rule through its rewordings.

## SOP checks

`GET /v1/workspaces/{ws}/sessions/{id}` returns `checks`, one per procedure the router considered.

- Each session is checked against the procedures its agent lists, directly or through a group, in the release it ran on, or the live release if it didn't say.
- Rules are identified by sopc's stable rule ids (`sopc export`, `lock.json`), so results line up across releases.

| Field | Type | |
|---|---|---|
| `procedure_id`, `procedure_name`, `procedure_version`, `release_id` | string, int | What was checked: the procedure file's version in that release. |
| `applicable` | bool | Whether the procedure applied to the session. |
| `excluded_reason` | string | It applied, but the session ended before the procedure started or finished, so it isn't checked. |
| `route_reason` | string | The router's one-sentence reason. |
| `result` | enum | `deviated`, `partial`, `followed` or `not_measurable`; null unless checked. |
| `checked_result` | enum | The checker's own result. `result` leaves out [suppressed](#review-and-suggestions) findings, so the two differ only when one is suppressed. |
| `goal_reached`, `goal_reason` | bool, string | Whether the procedure's goal was reached (null: unclear). |
| `steps_applicable`, `steps_done`, `nevers_violated` | int | Counts. Done includes out of order. |
| `model`, `prompt_version` | string | What produced it. |
| `checked_at` | datetime | When. |
| `rules` | list | Steps, then never rules, then warning signs. Fields below. |

Each entry of `rules` is `{rule_id, kind, index, text, status, evidence, reason, finding, finding_id, pattern_id, pattern_name, suppressed, override, correction}`. `correction` is the status a reviewer gave the rule, `{status, checked_status, source, note, reviewer, created_at}` (`source`: `correction`, `spot_check` or `override`), or null ([labels](#corrections-and-spot-checks)). `kind` is `step`, `never` or `warning`, `index` is 1-based within its kind, and each piece of evidence is `{turn, quote, source}`: `turn` indexes the session's `turns` (null for a tool call with no tool turn), and `source` is `transcript` or `tool_calls`.

```json
{
  "procedure_id": "allergen-check",
  "procedure_name": "Allergen check",
  "procedure_version": 1,
  "release_id": 4,
  "applicable": true,
  "excluded_reason": null,
  "route_reason": "The situation came up (0.95); the agent acted on it (0.99).",
  "result": "deviated",
  "checked_result": "deviated",
  "goal_reached": false,
  "goal_reason": "The agent never checked the allergen list and gave an unverified guess that the dish was safe.",
  "steps_applicable": 3,
  "steps_done": 2,
  "nevers_violated": 1,
  "model": "jev-1.13.0+anthropic/claude-sonnet-5-5",
  "prompt_version": "decide-1+check-5",
  "checked_at": "2026-10-20T22:09:47.557395Z",
  "rules": [
    {
      "rule_id": "ccc0048aa321", "kind": "step", "index": 1,
      "text": "Ask who the allergy is for and what it is",
      "status": "done",
      "evidence": [{"turn": 2, "quote": "The allergy is for your son, and it's peanuts, right?", "source": "transcript"}],
      "reason": "Decided: done (0.96).",
      "finding": false, "pattern_id": null, "pattern_name": null, "finding_id": null, "suppressed": false, "override": null
    },
    {
      "rule_id": "e09a4490b368", "kind": "never", "index": 1,
      "text": "Never say an item is \"allergen-free\" or \"safe\"",
      "status": "violated",
      "evidence": [{"turn": 4, "quote": "The mapo tofu is totally safe for peanut allergies, no worries.", "source": "transcript"}],
      "reason": "The agent told the caller the mapo tofu is \"totally safe for peanut allergies\".",
      "finding": true,
      "pattern_id": "3414bb1a-3f4b-4f5e-8691-f46e867bbed6",
      "pattern_name": "Called an item allergen-free or safe",
      "finding_id": "dd96eb76-6e0d-425d-adf8-59efd8df46b3",
      "suppressed": false, "override": null
    }
  ]
}
```

### Rule statuses

| Kind | Status | Means |
|---|---|---|
| Step | `done` | The agent did it. |
| Step | `out_of_order` | Done, but after a later step. Worked out from the first evidence turn of each done step: the fewest steps that explain the disorder. |
| Step | `skipped` | The session went past where it belonged without it. |
| Step | `not_reached` | The session ended before it. |
| Step | `not_applicable` | Its condition never arose. |
| Never | `violated`, `ok` | Broken, or not. |
| Warning sign | `present` | The situation came up and was handled as the sign says. |
| Warning sign | `unheeded` | It came up, the sign says what to do, and the agent didn't. Still counts as "present" in every sign-present count. |
| Warning sign | `absent` | It didn't come up. |

A warning sign is a condition, never a pass or fail, and never changes the result.

A step with `tool: x` and `required` is checked against the session's `tool_calls` when it has any. Called is `done`, and not called is `skipped`, unless the session never reached the step or it didn't apply. Without tool calls the transcript decides, and the evidence says so.

### Procedure result

| Result | When |
|---|---|
| `deviated` | A never rule was violated, a required step was skipped, or a step was skipped and the goal wasn't reached (or it's unclear). |
| `partial` | Otherwise: a step out of order or not reached, or a step that isn't required was skipped but the goal was reached anyway. That's leniency: the agent got there another way, so it's a gap, not a deviation. |
| `followed` | Every applicable step done, in order, and no never rule violated. |
| `not_measurable` | Nothing to judge: no applicable step and no never rule violated, or the agent never spoke. Left out of rates. |

A session's `result` is the worst of its procedures' results, in this order: deviated, partial, followed, not measurable.

### Findings

Findings are rule results with status `skipped`, `violated`, `out_of_order` or `unheeded` (`finding: true`): things the agent did or didn't do that someone may want to fix. They are stored in their own table and grouped into [patterns](#patterns-and-themes).

| | |
|---|---|
| Not a finding | `not_reached` (the session ended), and a warning sign that was present and handled. |
| Still a finding | A skipped step that leniency kept from making its procedure deviated. |
| Quotes | A `violated` never rule always quotes the agent's own words. One the model flagged without such a quote is stored as `ok`, with the reason saying so. |
| Re-running | Replaces a session's results. A run for content that has since been re-sent writes nothing. |

### How rules are checked

Routing and each rule are decisions first: yes or no questions with a probability. Jev answers them when it's on (`TYPESAFE_API_KEY`, `sopops-server[jev]`), and the `fast` model otherwise.

The `reasoning` model reviews rules the decisions flag (`skipped`, `violated`, `unheeded`) or aren't sure about. The rest keep the decision's status and evidence, and their `reason` starts with "Decided:".

| Run | `model` | `prompt_version` |
|---|---|---|
| Decision only | the decision model | `decide-1` |
| Decision, then review | `<decision model>+<reasoning model>` | `decide-1+check-5` |
| `SOPOPS_CHECK_MODE=review` (the reasoning model judges every rule) | the reasoning model | `check-5` |

| Setting | |
|---|---|
| `SOPOPS_DECISIONS` | JSON overriding any field: bands as `[no, yes, decide]` for `route`, `responded`, `condition`, `step`, `never` and `warning`; plus `goal` and `review_statuses`. |
| `SOPOPS_CHECK_REVIEW=off` | Let the decisions stand, with no reasoning model. |
| `SOPOPS_CHECK_MODE=review` | The reasoning model judges every rule. |

Thresholds fitted to a workspace's labeled sessions (`sopops_server.ai.programs.calibrate`) are stored per workspace and used for every question whose wording hasn't changed. They can widen a question's review band, never narrow it.

## Patterns and themes

Findings are grouped so the same mistake shows up once, with its count, instead of once per session.

| `kind` | What it holds |
|---|---|
| `rule` | Every finding on one rule, in every wording it has had ([Rule lineage](#rule-lineage)). Its `rule_id` is the lineage id, the id of the rule's first wording. |
| `sub` | One named failure mode of that rule. |
| `other` | Findings on a split rule that fit no failure mode. |

A finding's `pattern_id` is its most specific pattern.

| Stage | What happens |
|---|---|
| Named | A rule pattern is made with the rule's first finding and named then, once, by the reasoning model (`NamePattern`): what the agent did wrong, in a few plain words ("Didn't read the order back"). Without a model it takes the rule's text, trimmed to a short phrase, and the next recluster (or `sopops patterns rename [--workspace WS] [--all]`) names it with the model. |
| Shown | The UI puts "Step 3" or "Never rule 2" beside the name. Its `description`, and `rule.text`, are the rule as sessions ran it most recently. |
| Split | At 8 findings the reasoning model splits them into at most 5 named failure modes, plus an `other` sub-pattern. |
| Assigned | New findings on a split rule go to one of its failure modes, or to `other`, by a decision (Jev, or the fast model). |
| Reclustered | Split again when 25 findings arrived since the last clustering, or when `other` holds more than 25% of its findings with at least 5 new since, plus a daily cron. |
| Themed | The reasoning model groups a procedure's rule patterns by the phase of the session where they happen (opening, taking the order, closing), in session order. Existing theme names and ids are kept whenever they still fit. |

Reclustering keeps ids stable:

| | |
|---|---|
| Matched | Each new cluster is matched to the old one it shares the most findings with (Jaccard over finding ids, at least 0.3), and keeps its id, name and verdicts. |
| Retired | A failure mode that matches nothing is `retired`, not deleted. Its verdict carries over to the cluster that took most of its findings (Jaccard at least 0.3), unless that one has its own. |
| `clustered_at` | The last recluster that changed a sub-pattern's members materially: it was made then, or matched below a Jaccard of 0.5. |

| When it runs | |
|---|---|
| After a check | The pipeline assigns findings right away: inline, or the Hatchet `sopops_patterns` workflow, one run at a time per workspace. |
| Reclustering that is due | Right after that (inline), or in `sopops_recluster`. |
| Daily | `sopops_recluster_daily` (`SOPOPS_RECLUSTER_CRON`, default `0 6 * * *`). Without Hatchet, run `sopops recluster` from cron. |

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/patterns` | `{items, clustered_at}`: rule patterns with their `sub_patterns`, counted in the window (default 365 days; same window filters as Insights). Each has its `verdict`, `review_state` and `review_note` ([Review](#review-and-suggestions)) and `clustered_at`. The top-level `clustered_at` is the workspace's last clustering. Filters: `procedure`, `rule`. |
| `GET /v1/workspaces/{ws}/patterns/{id}` | One pattern: the rule it's about, its failure modes (for a rule pattern), example phrasings and its sessions in the window, plus `verdict_history`, `suppressed_count`, `suggestions`, `fix_release`, `fix_sessions` and `fix_sessions_needed`. |
| `POST /v1/workspaces/{ws}/patterns/recluster` | `{procedure?, rule?, force?}`: assign unassigned findings, recluster what's due (every rule with 8 or more findings with `force`), and regroup themes. Calls the models, synchronously. Returns `{assigned, reclustered, themed}`. |

`sopops recluster [--workspace WS] [--procedure P] [--rule R] [--force]` does the same from the command line.

```sh
curl -s -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/patterns?window=30d&procedure=allergen-check"
```

```json
{
  "items": [
    {
      "pattern_id": "3414bb1a-3f4b-4f5e-8691-f46e867bbed6",
      "procedure_id": "allergen-check",
      "rule_id": "e09a4490b368",
      "parent_id": null,
      "kind": "rule",
      "name": "Called an item allergen-free or safe",
      "description": "Never say an item is \"allergen-free\" or \"safe\"",
      "theme_id": "6fffe83c-d65c-4c64-802d-af3714a1398a",
      "theme_name": "Making safety claims about items",
      "status": "active",
      "findings": 4,
      "sessions": 4,
      "total_findings": 4,
      "suppressed": 0,
      "first_seen": "2026-10-19T07:48:18Z",
      "last_seen": "2026-10-20T17:08:18Z",
      "clustered_at": null,
      "rule": {"kind": "never", "index": 1, "text": "Never say an item is \"allergen-free\" or \"safe\""},
      "sub_patterns": [],
      "verdict": null,
      "review_state": "needs_review",
      "review_reason": "no_verdict",
      "review_note": "No verdict yet."
    }
  ],
  "clustered_at": "2026-10-19T13:22:09Z"
}
```

## Review and suggestions

A reviewer gives a pattern (a rule pattern or a failure mode) a **verdict**, with an optional note and a free-text `reviewer`.

| Verdict | Means | Its findings | Suggestion |
|---|---|---|---|
| `real` | A real agent mistake. | Keep counting. | Change the instructions to prevent it. |
| `acceptable` | Fine in this situation. | Suppressed, now and later. | Add the exception to the rule. |
| `sop_wrong` | The instruction is wrong or outdated. | Keep counting. | Edit the rule. |
| `checker_wrong` | sopops misjudged. | Suppressed, and negative calibration labels. | None. |

| | |
|---|---|
| Verdicts are appended, never edited | The latest is the pattern's verdict; the rest are its `verdict_history`. A failure mode without one has its rule pattern's (`inherited: true`). |
| An override | Says whether the checker got one finding right (`correct`), and optionally what actually happened (`status`: a finding status when it's right, another one when it's wrong). A wrong one is suppressed and becomes a negative label. A right one overrides a `checker_wrong` pattern for that finding. `acceptable` suppresses either way. Overrides are kept by session, procedure and rule, so they outlive a re-check. |
| A rule label | A correction or a spot-check answer on one rule ([below](#corrections-and-spot-checks)). On a finding it's a call like an override; the latest call on a rule wins. |
| Suppressed findings | Kept, flagged (`suppressed` on a session's rule results) and left out of every Insights count and rate. |
| Rolled up again | A procedure check's `result` is recomputed with its suppressed rules read as passed (`checked_result` keeps the checker's own), and the session's result is the worst of its checks. A session whose only deviation is suppressed counts as followed. |
| Counted on their own | Insights shows `suppressed` on tiles, scoreboard rows, ladder rows and patterns. |

```sh
curl -s -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/patterns/66567e7e-443b-4d60-9958-fc0eccc6b419/verdict" \
  -d '{"verdict": "real", "note": "Times come from the tool, not the agent.", "reviewer": "Ada", "suggest": true}'
```

```json
{
  "verdict_id": "b19d7faf-162e-43d0-b8d0-589ddd5489cb",
  "pattern_id": "66567e7e-443b-4d60-9958-fc0eccc6b419",
  "verdict": "real",
  "note": "Times come from the tool, not the agent.",
  "reviewer": "Ada",
  "carried_from": null,
  "inherited": false,
  "created_at": "2026-10-19T18:56:54Z",
  "suppressed_count": 0,
  "suggestion": {
    "suggestion_id": "eb68db6c-8701-4bbd-8eb0-7f43d8e0b05a",
    "pattern_id": "66567e7e-443b-4d60-9958-fc0eccc6b419",
    "procedure_id": "delivery-orders",
    "rule_id": "42789b07188d",
    "block_id": "delivery-orders",
    "status": "generating",
    "freshness": null
  }
}
```

### Corrections and spot checks

A reviewer can give any rule of a checked session its right status, finding or not: `POST /v1/workspaces/{ws}/sessions/{id}/rules/{procedure}/{rule}/label` with `{status, note?, reviewer?, source?}`. `status` must be one of the rule kind's ([statuses](#rule-statuses)); `source` is `correction` (default) or `spot_check`. Labels are kept by session, procedure and rule, so they outlive a re-check.

| The label says | On a rule | Then |
|---|---|---|
| A status that isn't a finding | With a finding | The finding is suppressed, as for an override marked wrong. |
| A finding status | With a finding | The finding is right: it overrides a `checker_wrong` pattern for it. |
| A finding status | Without one (`missed: true`) | Stored and shown on the session. No finding is added: the result, patterns and Insights keep what the checker found. |
| The checker's status | Without one | A confirmation (a spot check's usual answer). |

Every label is a calibration label. It returns `{label_id, session_id, procedure_id, rule_id, kind, checked_status, status, source, note, reviewer, created_at, missed, suppressed}`.

**Spot checks** come in batches of rule results with no finding, for a reviewer to check. `GET /v1/workspaces/{ws}/spot-checks` returns the current batch, `{batch_id, drawn_at, size, answered, corrections, done, items, default_batch}`; the workspace's first batch is drawn on the first request. Each item has the rule, the checker's `status`, `evidence` and `reason`, the session's `agent_id` and `started_at`, the decision before review (`decision_p`, `decision_threshold`, `decided_status`, `reviewed`), its `weight`, `why` (`close_call`, `review_changed` or `sample`), the `statuses` it can have, and `answered` and `answer`.

| | |
|---|---|
| Size | 25 by default. `GET` or `PUT /v1/workspaces/{ws}/spot-checks/settings` with `{batch}` (1 to 200; null for the default). |
| Drawing | `POST /v1/workspaces/{ws}/spot-checks/batches` with `{size?}`: a new batch replaces the current one. Only this draws one after the first. |
| Which | The most recent 5,000 results with no finding, never drawn or labeled before, grouped by procedure and agent. Each group is sorted by weight; the batch takes one from each group in turn, the group with the most valuable item first. |
| Weight | 1, plus up to 4 for a decision within 0.25 of its threshold (the closer, the more), plus 4 when the reviewer model changed the decision. Review mode (`SOPOPS_CHECK_MODE=review`) has no decisions, so its results weigh 1. |
| Answer | The rule's `label` route with `source: spot_check`. Any label on the rule after it was drawn answers it. `corrections` counts answers that differ from the checker. |
| Expiry | An item unanswered 7 days after it was drawn is dropped from the batch: not shown, not counted, never a label. |
| Filters | `agent`, `procedure`, `rule`, `since`, `until` (session start), `why`, and `sort` (`value`, the default, or `newest`). They narrow `items`; the progress counts the whole batch. |

### Review state

Each pattern has a `review_state`, with `review_reason` and a `review_note` saying why.

| State | When | `review_reason` |
|---|---|---|
| `needs_review` | No verdict. | `no_verdict` |
| `needs_review` | A verdict carried over by a recluster and not confirmed. | `carried_over` |
| `needs_review` | A verdict older than a recluster that changed the pattern's findings materially (it kept less than half of them). | `reclustered` |
| `needs_review` | An `acceptable` or `checker_wrong` verdict older than the rule's current wording, with findings on that wording. The verdict judged the old wording, so the new findings aren't suppressed until a verdict confirms it. | `reworded` |
| `open` | `real` or `sop_wrong`, with no suggestion applied for this verdict. | null |
| `fixing` | A suggestion for it was applied, as a draft or a pull request (open or merged). | null |
| `fixed` | 10 sessions on the fix's release or later, none with a finding in the pattern. | null |
| `regressed` | A finding in the pattern on a session that ran the fix's release or a later one. `review_note`: "Came back after release N". | `came_back` |
| `closed` | `acceptable` or `checker_wrong`. | null |

`fixing` waits for the release with the edit to be live, then for 10 sessions checked on the procedure on that release or later.

| | |
|---|---|
| The release with the edit | The first release whose file has the suggested edit, alone or merged with others. For a repo, the pull of the merge commit, then a publish. |
| `fix_release` | That release. Once it's live, `fix_sessions` counts those sessions and `fix_sessions_needed` is 10 (also on `fixed`). |
| A pull request closed without merging | Dismisses the suggestion, so the pattern is `open` again. |
| A reworded rule | The edit gives it a new sopc id. Its lineage keeps the new wording's findings in the same pattern, so a regression counts. |

The review queue lists `needs_review` and `regressed`; `state=open` lists verdicts waiting for a fix, and so on. A new verdict starts over.

### Suggestions

A **suggestion** is an edit to the pattern's procedure, made by the reasoning model (`SuggestEdits`) from the procedure's file at head, the rule, the pattern, the verdict and note, and up to 5 of its findings.

| | |
|---|---|
| Compiling | The edited file must compile with the rest of head. One that doesn't is sent back once with sopc's issues, then marked `failed`. |
| `plan.agents` | Every agent whose instructions change, with a diff. |
| Generated | As a job: inline after the response, or the Hatchet `sopops_suggest` workflow. |
| `status` | `generating`, then `proposed` or `failed`. A proposal becomes `applied`, `pr_open` or `dismissed`. |
| Apply | A pull request when the workspace has a GitHub repo connection (`pr_open`, `pr_url`). Otherwise the file is saved as a draft (`applied`), which shows in Unpublished changes and goes out with the next publish. |
| Following a pull request | The `sopops_pr_status` cron, every 15 minutes (`SOPOPS_PR_STATUS_CRON`; without Hatchet, `sopops pr-status` from cron), and reading the suggestion, at most once a minute. Merged makes it `merged`. Closed without merging makes it `dismissed`, with the note "PR closed". |

When the procedure changed since a suggestion was written, its edit is merged into the file as it is now: a three-way line merge (`sopops_server.merge`) with the file it was written against as the base.

| Merging | |
|---|---|
| Merges | Edits on different lines, neighbouring lines included. |
| Conflicts | The same lines changed two ways, or two insertions at the same place. |
| Checked after | A clean merge must compile with the rest of head and still define the procedure, or it's a conflict too. |

Each proposed suggestion has `freshness`, computed when it's read (in every list, the review queue and the pattern page too):

| `freshness` | Means | Apply |
|---|---|---|
| `current` | The file is the one it was written against. | Writes `new_text`. |
| `merges` | The file changed; the edit merges in and the result compiles. | Writes the merged file (`since_written.merged_text`) and records `merged_into_version`. A pull request gets the merged file too. |
| `conflict` | The same lines were changed, or the merged file doesn't compile. | `409`, saying why. Rewrite it instead. |

**Rewrite** (`POST /v1/workspaces/{ws}/suggestions/{id}/rewrite`, on a proposed one) makes a new suggestion for the same pattern, from the file as it is now.

- The edits made since the old one was written go to the model as a unified diff, to keep and build on (`changes_since`, prompt version `suggest-2`).
- The old one is `dismissed` with the note "Rewritten against the current text." and `rewritten_as`; the new one has `rewrite_of`.
- Each suggestion stays one proposal against one base file, so its history is kept as it was.

### Calibration

Calibration labels come from verdicts, overrides, corrections and spot checks, per workspace and only from the database.

| | |
|---|---|
| A labeled session | Any session with a reviewed finding on a procedure, or a rule label on it, for that procedure's `check:<procedure>` thresholds. |
| A negative label | A finding judged wrong. |
| A missed mistake | A rule labeled with a finding status the checker didn't flag: a failure. |
| A status a reviewer gave | Read in place of the checker's when the session's result is worked out. |
| An earlier wording | A label on one labels the rule as the fitted release words it (its lineage). |

`POST /v1/workspaces/{ws}/calibrate` (or `sopops calibrate --workspace WS [--procedure P] [--dry-run]`) fits them (`sopops_server.ai.programs.calibrate`), and returns per procedure the labels used and each question's threshold before and after. It runs as a job: `sopops_calibrate` on Hatchet with the API waiting for it, or else in the API process.

A procedure with fewer than 5 labeled sessions, or no rule with 3 labeled failures, is refused. `skipped` is then `{code, message, have, need}`:

| `code` | On |
|---|---|
| `too_few_labeled_sessions` | A procedure with fewer than 5 labeled sessions. |
| `too_few_failures` | A procedure with no rule with 3 judged failures, or a question whose rule has too few. |
| `not_in_release` | A procedure that isn't in the release being fitted. |
| `too_alike` | A question whose answers don't spread enough to fit. |
| `held_out` | A question whose new value didn't hold on held-out sessions. |

### Endpoints

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/review` | The review queue. Fields below. |
| `POST /v1/workspaces/{ws}/calibrate` | `{procedure?, dry_run?}`: fit the check thresholds to the labels. Returns `{workspace, dry_run, procedures: [{procedure_id, program, labels, negative_labels, saved, skipped, score_before, score, rules: [{rule_id, lineage_id, kind, index, text, field, before, after, skipped}]}]}`. `text` is the rule as it reads now (its lineage's latest wording). |
| `POST /v1/workspaces/{ws}/patterns/{id}/verdict` | `{verdict, note?, reviewer?, suggest?: true}`. Returns the verdict with `suppressed_count`, and `suggestion` when one was queued (`suggest` and a real, acceptable or sop_wrong verdict). |
| `POST /v1/workspaces/{ws}/findings/{id}/override` | `{correct, status?, note?, reviewer?}`. Returns the override and whether the finding is now `suppressed`. `422` for a status the rule can't have, or one that doesn't fit `correct`. |
| `POST /v1/workspaces/{ws}/sessions/{id}/rules/{procedure}/{rule}/label` | `{status, note?, reviewer?, source?}`: a correction or a spot-check answer. `404` for a rule the session wasn't checked on, `422` for a status its kind doesn't have. |
| `GET /v1/workspaces/{ws}/spot-checks` | The current spot-check batch, with filters. |
| `POST /v1/workspaces/{ws}/spot-checks/batches` | `{size?}`: draw the next batch. |
| `GET`, `PUT /v1/workspaces/{ws}/spot-checks/settings` | `{batch, default}`; `PUT {batch}`. |
| `GET /v1/workspaces/{ws}/patterns/{id}/regression` | A regression set for the pattern, as JSON Lines. Fields below. `409` without a verdict. |
| `POST /v1/workspaces/{ws}/patterns/{id}/suggest` | (Re)generate a suggestion for the pattern's verdict. Returns it, `generating`. `409` unless the verdict is real, acceptable or sop_wrong. |
| `GET /v1/workspaces/{ws}/suggestions?status=` | Newest first, with each one's `created_at`, `applied_at` (saved as a draft), `pr_opened_at`, `merged_at` (when sopops saw it merged), and `released_in` and `released_at`: the first release whose file has its edit. A proposed one has `freshness`. |
| `GET /v1/workspaces/{ws}/suggestions/{id}` | One suggestion. Fields below. |
| `POST /v1/workspaces/{ws}/suggestions/{id}/apply` | `{reviewer?}`. A pull request or a draft edit, merged with changes made since when they don't overlap. `409` when it isn't proposed or is in conflict. |
| `POST /v1/workspaces/{ws}/suggestions/{id}/rewrite` | `{reviewer?}`. A new suggestion against the procedure as it is now. Returns it, `generating`. `409` unless it's proposed. |
| `POST /v1/workspaces/{ws}/suggestions/{id}/dismiss` | `{note?, reviewer?}`. |

**`GET /review`** returns `{items, total, counts, clustered_at}`: active leaf patterns (a rule pattern that isn't split, else its failure modes and `other`) in the review states asked for, most sessions first, with `counts` per state. Query: `state` (repeatable; default `needs_review` and `regressed`) and `limit` (default 50, at most 500).

| Item field | |
|---|---|
| `theme_name`, `procedure_name`, `rule_text` | Where the pattern sits. |
| `examples` | Up to 5 findings, newest first (suppressed ones last): `finding_id`, `session_id`, `started_at`, `status`, `excerpt`, `reason`, `evidence`, `suppressed`. |
| `verdict` | The current verdict, or null. |
| `review_state`, `review_reason`, `review_note` | As above. |
| `fix_release`, `fix_sessions`, `fix_sessions_needed` | The fix and its progress. |
| `new_findings`, `suppressed_count` | Findings that aren't suppressed since the verdict, and suppressed ones. |
| `suggestion` | Its latest suggestion, or null. |

```json
{
  "items": [
    {
      "pattern_id": "3414bb1a-3f4b-4f5e-8691-f46e867bbed6",
      "procedure_id": "allergen-check",
      "procedure_name": "Allergen check",
      "rule_id": "e09a4490b368",
      "kind": "rule",
      "name": "Called an item allergen-free or safe",
      "rule": {"kind": "never", "index": 1, "text": "Never say an item is \"allergen-free\" or \"safe\""},
      "rule_text": "Never say an item is \"allergen-free\" or \"safe\"",
      "findings": 4,
      "sessions": 4,
      "examples": [
        {
          "finding_id": "dd96eb76-6e0d-425d-adf8-59efd8df46b3",
          "session_id": "01ddf3cc-fa1d-4b90-a645-f554cd873d24",
          "started_at": "2026-10-20T17:08:18Z",
          "status": "violated",
          "excerpt": "The mapo tofu is totally safe for peanut allergies, no worries.",
          "reason": "The agent told the caller the mapo tofu is \"totally safe for peanut allergies\".",
          "evidence": [{"turn": 4, "quote": "The mapo tofu is totally safe for peanut allergies, no worries.", "source": "transcript"}],
          "suppressed": false
        }
      ],
      "verdict": null,
      "new_findings": 4,
      "review_state": "needs_review",
      "review_reason": "no_verdict",
      "review_note": "No verdict yet.",
      "fix_release": null,
      "fix_sessions": null,
      "fix_sessions_needed": null,
      "suppressed_count": 0,
      "suggestion": null
    }
  ],
  "total": 6,
  "counts": {"needs_review": 6, "open": 1, "fixing": 2, "fixed": 2, "regressed": 0, "closed": 0},
  "clustered_at": "2026-10-19T13:22:09Z"
}
```

**`GET /patterns/{id}/regression`** returns one JSON line per finding in the pattern: `session_id`, `external_id`, `transcript_ref` (the session's URL), `procedure_id`, `rule_id`, `rule_kind`, `rule_text`, `verdict`, `observed_status`, `expected_status`, `expect`.

| `expect` | On | What to do |
|---|---|---|
| `agent` | A `real` mistake. | Replay the conversation against the fixed instructions; the rule should now pass. |
| `checker` | `acceptable`, `sop_wrong`, `checker_wrong`, or a finding overridden as wrong. | Re-check the same transcript; the rule should come out `expected_status` (`done`, `ok`, or `present` for a warning sign). |

**`GET /suggestions/{id}`** has `path`, `base_version`, `old_text`, `new_text`, `rationale`, `plan` (`agents`: `agent`, `added`, `removed`, `diff`), `pattern`, `verdict`, `status`, `pr_url`, `error`, `attempts`, `model`, `prompt_version`, `merged_into_version`, `rewrite_of` and `rewritten_as`. A proposed one also has `since_written`:

| Field | |
|---|---|
| `state` | Its `freshness`. |
| `head_version` | The file's version now. |
| `changes` | A unified diff of the file since the suggestion was written. |
| `merged_text` | For `merges`. |
| `conflicts` | For `conflict`: each part's `line` in the file now, and its `base`, `current` and `suggested` lines. |
| `issues` | For `conflict`: sopc's errors with the merged file. |

```json
{
  "suggestion_id": "eb68db6c-8701-4bbd-8eb0-7f43d8e0b05a",
  "pattern_id": "66567e7e-443b-4d60-9958-fc0eccc6b419",
  "procedure_id": "delivery-orders",
  "rule_id": "42789b07188d",
  "status": "proposed",
  "rationale": "Expanded the Never rule to cover ready and preparation time estimates as well as delivery times.",
  "path": "procedures/delivery-orders.md",
  "base_version": 4,
  "plan": {"agents": [{"agent": "copper-wok-eastside", "added": 1, "removed": 1, "diff": "--- copper-wok-eastside (now)\n+++ ..."}]},
  "attempts": 1,
  "model": "anthropic/claude-sonnet-5-5",
  "prompt_version": "suggest-2",
  "freshness": "conflict",
  "since_written": {
    "state": "conflict",
    "head_version": 5,
    "changes": "--- procedures/delivery-orders.md (when written)\n+++ ...",
    "merged_text": null,
    "conflicts": [
      {
        "line": 12,
        "base": "- Never tell the caller a delivery time\n",
        "current": "- Never tell the caller a delivery time. This includes estimates.\n",
        "suggested": "- Never tell the caller a delivery time. This includes any time the order will be ready.\n"
      }
    ],
    "issues": null
  }
}
```

## Rule lineage

sopc derives a rule's id from its text, so a reworded rule (by hand, or by an applied suggestion) has a new id. Sessions and findings keep the exact rule id and wording they ran. sopops records which rule each reworded rule replaced (`rule_lineage`).

| What follows the chain | |
|---|---|
| Its rule pattern | Keyed by the lineage id (the first wording's id), and so its verdicts, review states and failure modes. |
| Suggestions | Made for the rule as it reads at head. |
| Insights | Rows, trends, the rule page and release compare. |
| Sessions | The `rule` filter. |
| Calibration | Its labels. |

**At publish**, each procedure's rules in the release that was live are compared with the new release's. A rule whose id is in both continues. Changed rules are matched old to new, one to one, within one procedure and one kind (step, never rule, warning sign), in this order:

1. `suggestion`: an applied suggestion (a draft, or a pull request open or merged) whose edit is in the file published knows the rule it edited. Its new wording is the new rule at that position, else the most similar new one.
2. `matched`: the new rule at the same position, if the two wordings' similarity is at least 0.35.
3. `matched`: the most similar remaining pair anywhere in the procedure, best first, if at least 0.5 (moved and reworded).

Similarity is token Jaccard over lowercase words, without a few function words (`a`, `the`, `to`, `you`). On the test fixture's and the walkthrough's procedures, rewordings score 0.38 to 1, and two different rules of one procedure score at most 0.29.

An unmatched new rule is new, and an unmatched old one is removed. A wording that comes back (a revert) stays in its own lineage. Links are never overwritten by later publishes.

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/rules/{rule_id}/lineage?procedure=` | `{procedure_id, rule_id, lineage_id, previous_rule_id, how, rules}`, oldest first. Pass `procedure` only when the id is in more than one procedure. |
| `POST /v1/workspaces/{ws}/rules/{rule_id}/lineage` | `{previous_rule_id, procedure_id?}`: the rule replaced `previous_rule_id` (a `manual` link). `409` for a cycle. |
| `DELETE /v1/workspaces/{ws}/rules/{rule_id}/lineage?procedure=` | The rule is a new rule, kept as a `manual` row with no previous rule. Its findings, and the later wordings', move to a rule pattern of their own, with no verdict. |
| `POST /v1/workspaces/{ws}/rules/lineage/rebuild` | Recompute the `matched` and `suggestion` links of every release against the one before it by number (manual rows stay), and move patterns to match. Idempotent. Returns `{workspace, links, procedures}`. |

```sh
curl -s -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/rules/4bc8da680fd7/lineage"
```

```json
{
  "procedure_id": "delivery-orders",
  "rule_id": "4bc8da680fd7",
  "lineage_id": "4bc8da680fd7",
  "previous_rule_id": null,
  "how": null,
  "rules": [
    {
      "rule_id": "4bc8da680fd7", "previous_rule_id": null, "how": null, "similarity": null,
      "release_id": null, "text": "Read back the full order and the total, and ask the caller to confirm.", "from_release": 2
    },
    {
      "rule_id": "2253420ea2ad", "previous_rule_id": "4bc8da680fd7", "how": "suggestion", "similarity": 0.4737,
      "release_id": 4, "text": "Read back the full order and the total... The total must be an actual amount in dollars.", "from_release": 4
    }
  ]
}
```

Linking a rule by hand moves its findings into the earlier rule's pattern. When both had a pattern they merge into the older one: failure modes, verdicts (whose latest then counts) and suggestions move over. If the older one is split, the findings wait in `other` for the next recluster.

`sopops lineage rebuild [--workspace WS]` does the rebuild from the command line. Migration 0013 ran it on every workspace when it added the table; it needs the sopc binary, and without one it says to run the command.
