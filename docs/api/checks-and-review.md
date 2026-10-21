# Checks and review

How sopops checks each session against its procedures, groups the same mistake across sessions, and turns a reviewer's verdict into a suggested fix. The last section covers reworded rules.

## SOP checks

Each session is checked against the procedures its agent lists (directly or through a group) in the release it ran on, or the live release if it didn't say. Rules are identified by sopc's stable rule ids (`sopc export`, `lock.json`), so results line up across releases.

`GET /v1/workspaces/{ws}/sessions/{id}` returns `checks`, one per procedure the router considered:

| Field | |
|---|---|
| `procedure_id`, `procedure_name`, `procedure_version`, `release_id` | What was checked: the procedure file's version in that release. |
| `applicable` | Whether the procedure applied to the session. |
| `excluded_reason` | It applied, but the session ended before the procedure started or finished, so it isn't checked. |
| `route_reason` | The router's one-sentence reason. |
| `result` | `deviated`, `partial`, `followed` or `not_measurable`; null unless checked. |
| `checked_result` | The checker's own result. `result` leaves out [suppressed](#review-and-suggestions) findings, so the two differ only when one is suppressed. |
| `goal_reached`, `goal_reason` | Whether the procedure's goal was reached (null: unclear). |
| `steps_applicable`, `steps_done`, `nevers_violated` | Counts. Done includes out of order. |
| `model`, `prompt_version` | What produced it. |
| `rules` | `[{rule_id, kind, index, text, status, evidence: [{turn, quote, source}], reason, finding, finding_id, pattern_id, suppressed, override}]`: steps, then nevers, then warning signs. `turn` indexes `turns`; `source` is `transcript` or `tool_calls`. |

### Rule statuses

| Kind | Statuses |
|---|---|
| Step | `done`; `out_of_order` (done, but after a later step: the fewest steps that explain the order of their first evidence turns); `skipped` (the session went past where it belonged without it); `not_reached` (the session ended before it); `not_applicable` (its condition never arose). |
| Never | `violated` or `ok`. |
| Warning sign | `present` (the situation came up and was handled as the sign says), `unheeded` (it came up, the sign says what to do, and the agent didn't) or `absent`. A warning sign is a condition, never a pass or fail. `unheeded` still counts as "present" in every sign-present count. |

A step with `tool: x` and `required` is checked against the session's `tool_calls` when it has any. Called is `done`; not called is `skipped`, unless the session never reached the step or it didn't apply. Without tool calls the transcript decides, and the evidence says so.

### Procedure result

| Result | When |
|---|---|
| `deviated` | A never rule was violated, a required step was skipped, or a step was skipped and the goal wasn't reached (or it's unclear). |
| `partial` | Otherwise: a step out of order or not reached, or a step that isn't required was skipped but the goal was reached anyway. That's leniency: the agent got there another way, so it's a gap, not a deviation. |
| `followed` | Every applicable step done, in order, and no never rule violated. |
| `not_measurable` | Nothing to judge: no applicable step and no never rule violated, or the agent never spoke. Left out of rates. |

A session's `result` is the worst of its procedures' results, in this order: deviated, partial, followed, not measurable.

### Findings

Findings are rule results with status `skipped`, `violated`, `out_of_order` or `unheeded` (`finding: true`): things the agent did or didn't do that someone may want to fix.
- `not_reached` isn't a finding (the session ended), and neither is a warning sign that was present and handled.
- A `violated` never rule always quotes the agent's own words. One the model flagged without such a quote is stored as `ok`, with the reason saying so.
- A skipped step that leniency kept from making its procedure deviated is still a finding.

Findings are stored in their own table and grouped into [patterns](#patterns-and-themes). Re-running a session replaces its results; a run for content that has since been re-sent writes nothing.

### How rules are checked

Routing and each rule are decisions first: yes or no questions with a probability. Jev answers them when it's on (`TYPESAFE_API_KEY`, `sopops-server[jev]`), and the `fast` model otherwise. The `reasoning` model reviews rules the decisions flag (`skipped`, `violated`, `unheeded`) or aren't sure about. The rest keep the decision's status and evidence, and their `reason` starts with "Decided:".

`model` and `prompt_version` say which ran:

| Run | `model` | `prompt_version` |
|---|---|---|
| Decision only | the decision model | `decide-1` |
| Decision, then review | `<decision model>+<reasoning model>` | `decide-1+check-5` |
| `SOPOPS_CHECK_MODE=review` (the reasoning model judges every rule) | the reasoning model | `check-5` |

Settings: `SOPOPS_DECISIONS` (JSON: bands as `[no, yes, decide]` for `route`, `responded`, `condition`, `step`, `never`, `warning`; `goal`; `review_statuses`), and `SOPOPS_CHECK_REVIEW=off`.

Thresholds fitted to a workspace's labeled sessions (`sopops.ai.programs.calibrate`) are stored per workspace and used for every question whose wording hasn't changed. They can widen a question's review band, never narrow it.

## Patterns and themes

Findings are grouped so the same mistake shows up once, with its count, instead of once per session.

- **Rule pattern** (`kind: rule`): every finding on one rule, in every wording it has had ([Rule lineage](#rule-lineage)). Its `rule_id` is the lineage id, the id of the rule's first wording.
  - It's created with the rule's first finding and named then, once, by the reasoning model (`NameRulePattern`): what the agent did wrong, in a few plain words ("Volunteered a price or description", "Didn't read the order back").
  - Without a model it's named with the rule's text, trimmed to a short phrase. The next recluster (or `sopops patterns rename [--workspace WS] [--all]`) names those with the model.
  - The UI shows "Step 3" or "Never rule 2" beside the name. Its `description`, and `rule.text`, are the rule as sessions ran it most recently (its lineage's current wording).
- **Failure modes** (`kind: sub`): once a rule has 8 findings, the reasoning model splits them into at most 5 named ways the agent failed it, plus an `other` sub-pattern (`kind: other`) for findings that fit none. New findings on a split rule are assigned to one of its failure modes, or to `other`, by a decision (Jev, or the fast model).
- **Reclustering**: a rule is split again when 25 findings arrived since its last clustering, or when `other` holds more than 25% of its findings (with at least 5 new since), plus a daily cron.
  - New clusters are matched to the old ones by member overlap (Jaccard over finding ids, at least 0.3), so a matched failure mode keeps its id, name and verdicts.
  - A failure mode that matches nothing is `retired`, not deleted. Its verdict carries over to the new cluster that took most of its findings (Jaccard at least 0.3), unless that one has its own.
- **Themes**: the reasoning model groups a procedure's rule patterns by the phase of the session where they happen (opening, taking the order, closing...), in session order. Existing theme names and ids are kept whenever they still fit.

A finding's `pattern_id` is its most specific pattern.

**When it runs.** The pipeline assigns findings right after a session is checked (inline, or the Hatchet `sopops_patterns` workflow, one run at a time per workspace). Due reclustering runs right after (inline) or in `sopops_recluster`. The daily cron is `sopops_recluster_daily` (`SOPOPS_RECLUSTER_CRON`, default `0 6 * * *`); without Hatchet, run `sopops recluster` from cron.

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/patterns` | `{items, clustered_at}`: rule patterns with their `sub_patterns`, counted in the window (default 365 days; same window filters as Insights). Each has its `verdict`, `review_state` and `review_note` ([Review](#review-and-suggestions)) and `clustered_at`. The top-level `clustered_at` is the workspace's last clustering. Filters: `procedure`, `rule`. |
| `GET /v1/workspaces/{ws}/patterns/{id}` | One pattern: the rule it's about, its failure modes (for a rule pattern), example phrasings and its sessions in the window. |
| `POST /v1/workspaces/{ws}/patterns/recluster` | `{procedure?, rule?, force?}`: assign unassigned findings, recluster what's due (every rule with 8+ findings with `force`), and regroup themes. Calls the models, synchronously. Returns `{assigned, reclustered, themed}`. |

`sopops recluster [--workspace WS] [--procedure P] [--rule R] [--force]` does the same from the command line.

## Review and suggestions

A reviewer gives a pattern (a rule pattern or a failure mode) a **verdict**, with an optional note and a free-text `reviewer`:

| Verdict | Means | Its findings | Suggestion |
|---|---|---|---|
| `real` | A real agent mistake. | Keep counting. | Change the instructions to prevent it. |
| `acceptable` | Fine in this situation. | Suppressed, now and later. | Add the exception to the rule. |
| `sop_wrong` | The instruction is wrong or outdated. | Keep counting. | Edit the rule. |
| `checker_wrong` | sopops misjudged. | Suppressed, and negative calibration labels. | None. |

- **Verdicts are appended, never edited.** The latest is the pattern's verdict; the rest are its `verdict_history`. A failure mode without one has its rule pattern's (`inherited: true`).
- **An override** says whether the checker got one finding right (`correct`). A wrong one is suppressed and becomes a negative label. A right one overrides a `checker_wrong` pattern for that finding. `acceptable` suppresses either way. Overrides are kept by session and rule, so they outlive a re-check.
- **Suppressed findings** are kept, flagged (`suppressed` on a session's rule results) and left out of every Insights count and rate. A procedure check's `result` is rolled up again with its suppressed rules read as passed (`checked_result` keeps the checker's own), and the session's result is the worst of its checks. So a session whose only deviation is suppressed counts as followed. Insights shows suppressed counts on their own (`suppressed` on tiles, scoreboard rows, ladder rows and patterns).

### Calibration

Calibration labels come from verdicts and overrides, per workspace and only from the database. Every session with a reviewed finding on a procedure is a labeled session for its `check:<procedure>` thresholds; a finding judged wrong is a negative label. A label on an earlier wording of a rule labels the rule as the fitted release words it (its lineage).

`POST /v1/workspaces/{ws}/calibrate` (or `sopops calibrate --workspace WS [--procedure P] [--dry-run]`) fits them (`sopops.ai.programs.calibrate`). It returns, per procedure, the labels used and each question's threshold before and after. It runs as a job: `sopops_calibrate` on Hatchet with the API waiting for it, or else in the API process.

A procedure with fewer than 5 labeled sessions, or no rule with 3 labeled failures, is refused. `skipped` is then `{code, message, have, need}`, with the code `too_few_labeled_sessions`, `too_few_failures` or `not_in_release`. A question that keeps its threshold says why the same way (`too_few_failures` on its rule, `too_alike`, `held_out`).

### Review state

Each pattern has a `review_state`, with a `review_note` saying why:

| State | When |
|---|---|
| `needs_review` | No verdict; a verdict carried over by a recluster and not confirmed; a verdict older than a recluster that changed the pattern's findings materially (it kept less than half of them); or an `acceptable` or `checker_wrong` verdict older than the rule's current wording, with findings on that wording. In that last case the verdict judged the old wording, so the new findings aren't suppressed until a verdict confirms it. |
| `open` | `real` or `sop_wrong`, with no suggestion applied for this verdict. |
| `fixing` | A suggestion for it was applied, as a draft or a pull request (open or merged). See below. |
| `fixed` | 10 such sessions, none with a finding in the pattern. |
| `regressed` | A finding in the pattern on a session that ran the fix's release or a later one (`review_note`: "Came back after release N"). The edit gives the rule a new sopc id; its lineage keeps the new wording's findings in the same pattern, so they count. |
| `closed` | `acceptable` or `checker_wrong`. |

**`fixing`** waits for the release with the edit to be live, then for 10 sessions checked on the procedure on that release or later. The release with the edit is the first release whose file has the suggested edit, alone or merged with others; for a repo, it's the pull of the merge commit, then a publish. `fix_release` is that release. Once it's live, `fix_sessions` counts those sessions and `fix_sessions_needed` is 10 (also on `fixed`).

The review queue lists `needs_review` and `regressed`; `state=open` lists verdicts waiting for a fix, and so on. A new verdict starts over.

### Suggestions

A **suggestion** is an edit to the pattern's procedure, made by the reasoning model (`SuggestEdit`) from the procedure's file at head, the rule, the pattern, the verdict and note, and up to 5 of its findings.
- It must compile with the rest of head. One that doesn't is sent back once with sopc's issues, then marked `failed`.
- `plan.agents` lists every agent whose instructions change, with a diff.
- It's generated as a job: inline after the response, or the Hatchet `sopops_suggest` workflow.
- Status: `generating`, then `proposed` or `failed`. A proposal becomes `applied`, `pr_open` or `dismissed`.
- **Apply** opens a pull request when the workspace has a GitHub repo connection (`pr_open`, `pr_url`). Otherwise it saves the file as a draft (`applied`), which shows in Unpublished changes and goes out with the next publish.
- A pull request is followed on GitHub every 15 minutes (`sopops_pr_status` on Hatchet, `SOPOPS_PR_STATUS_CRON`; without Hatchet, `sopops pr-status` from cron) and when the suggestion is read (at most once a minute). Merged makes it `merged`. Closed without merging makes it `dismissed`, with the note "PR closed".

**When the procedure changed since a suggestion was written** (another suggestion was applied, or someone edited the file), its edit is merged into the file as it is now. That's a three-way line merge (`sopops.merge`) with the file it was written against as the base. Edits on different lines merge, neighbouring lines included. The same lines changed two ways, or two insertions at the same place, conflict. A clean merge is then checked like a proposal: it must compile with the rest of head and still define the procedure, or it's a conflict too.

Each proposed suggestion has `freshness`, computed when it's read (in every list, the review queue and the pattern page too):

| `freshness` | Means | Apply |
|---|---|---|
| `current` | The file is the one it was written against. | Writes `new_text`. |
| `merges` | The file changed; the edit merges in and the result compiles. | Writes the merged file (`since_written.merged_text`) and records `merged_into_version`. A pull request gets the merged file too. |
| `conflict` | The same lines were changed, or the merged file doesn't compile. | `409`, saying why. **Rewrite** it instead. |

**Rewrite** (`POST /v1/workspaces/{ws}/suggestions/{id}/rewrite`, on a proposed one) makes a new suggestion for the same pattern. It's generated like any other, but from the file as it is now, with the edits made since the old one was written (a unified diff) given to the model to keep and build on (`changes_since`, prompt version `suggest-2`). The old one is `dismissed` with the note "Rewritten against the current text." and `rewritten_as`; the new one has `rewrite_of`. Each suggestion stays one proposal against one base file, so its history is kept as it was.

### Endpoints

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/review` | The review queue. See below. |
| `POST /v1/workspaces/{ws}/calibrate` | `{procedure?, dry_run?}`: fit the check thresholds to the labels ([Calibration](#calibration)). Returns `{workspace, dry_run, procedures: [{procedure_id, labels, negative_labels, saved, skipped, score_before, score, rules: [{rule_id, lineage_id, kind, index, text, field, before, after, skipped}]}]}`. `text` is the rule as it reads now (its lineage's latest wording). |
| `POST /v1/workspaces/{ws}/patterns/{id}/verdict` | `{verdict, note?, reviewer?, suggest?: true}`. Returns the verdict with `suppressed_count`, and `suggestion` when one was queued (`suggest` and a real, acceptable or sop_wrong verdict). |
| `POST /v1/workspaces/{ws}/findings/{id}/override` | `{correct, note?, reviewer?}`. Returns the override and whether the finding is now `suppressed`. |
| `GET /v1/workspaces/{ws}/patterns/{id}` | Also has `verdict`, `verdict_history`, `review_state`, `review_note`, `fix_release`, `fix_sessions`, `fix_sessions_needed`, `suppressed_count` (all time) and `suggestions`. |
| `GET /v1/workspaces/{ws}/patterns/{id}/regression` | A regression set for the pattern, as JSON Lines. See below. `409` without a verdict. |
| `POST /v1/workspaces/{ws}/patterns/{id}/suggest` | (Re)generate a suggestion for the pattern's verdict. Returns it, `generating`. `409` unless the verdict is real, acceptable or sop_wrong. |
| `GET /v1/workspaces/{ws}/suggestions?status=` | Newest first. See below. |
| `GET /v1/workspaces/{ws}/suggestions/{id}` | One suggestion. See below. |
| `POST /v1/workspaces/{ws}/suggestions/{id}/apply` | `{reviewer?}`. A pull request or a draft edit (above), merged with changes made since when they don't overlap. `409` when it isn't proposed or is in conflict. |
| `POST /v1/workspaces/{ws}/suggestions/{id}/rewrite` | `{reviewer?}`. A new suggestion against the procedure as it is now (above). Returns it, `generating`. `409` unless it's proposed. |
| `POST /v1/workspaces/{ws}/suggestions/{id}/dismiss` | `{note?, reviewer?}`. |

**`GET /review`** returns `{items, total, counts, clustered_at}`: active leaf patterns (a rule pattern that isn't split, else its failure modes and `other`) in the review states asked for, most sessions first, with `counts` per state. Query: `state` (repeatable; default `needs_review` and `regressed`) and `limit` (default 50). Each item has:
- its theme, procedure name and rule text;
- 5 example findings (`finding_id`, `session_id`, `started_at`, `excerpt`, `evidence`);
- its `verdict` (or null), `review_state`, `review_reason` (`no_verdict`, `carried_over`, `reclustered`, `reworded`, `came_back`; null otherwise) and `review_note`;
- `fix_release`, `fix_sessions`, `fix_sessions_needed`, `new_findings` and `suppressed_count`;
- its latest `suggestion` (or null).

**`GET /patterns/{id}/regression`** returns one JSON line per finding in the pattern: `session_id`, `external_id`, `transcript_ref` (the session's URL), `procedure_id`, `rule_id`, `rule_kind`, `rule_text`, `verdict`, `observed_status`, `expected_status`, `expect`.
- `expect: agent` (a real mistake): replay the conversation against the fixed instructions, and the rule should now pass.
- `expect: checker` (acceptable, sop_wrong, checker_wrong, or a finding overridden as wrong): re-check the same transcript, and the rule should come out `expected_status` (`done`, `ok`, or `present` for a warning sign).

**`GET /suggestions`** lists each suggestion with its dates: `created_at`, `applied_at` (saved as a draft), `pr_opened_at`, `merged_at` (when sopops saw it merged), and `released_in` and `released_at`: the first release whose file has its edit (the suggested text, or that merged with other edits). A proposed one has `freshness` (above).

**`GET /suggestions/{id}`** has `old_text`, `new_text`, `rationale`, `plan` (`agents`: `agent`, `added`, `removed`, `diff`), `pattern`, `verdict`, `status`, `pr_url`, `error`, `merged_into_version`, `rewrite_of` and `rewritten_as`. A proposed one also has `since_written`:

| Field | |
|---|---|
| `state` | Its `freshness`. |
| `head_version` | The file's version now. |
| `changes` | A unified diff of the file since the suggestion was written. |
| `merged_text` | For `merges`. |
| `conflicts` | For `conflict`: each part's `line` in the file now, and its `base`, `current` and `suggested` lines. |
| `issues` | For `conflict`: sopc's errors with the merged file. |

## Rule lineage

sopc derives a rule's id from its text, so a reworded rule (by hand, or by an applied suggestion) has a new id. Sessions and findings keep the exact rule id and wording they ran. sopops records which rule each reworded rule replaced (`rule_lineage`), and everything about a rule follows the chain:
- its rule pattern, keyed by the lineage id (the first wording's id), and so its verdicts, review states and failure modes;
- suggestions, made for the rule as it reads at head;
- Insights rows, trends, the rule page and release compare;
- the sessions filter `rule`;
- calibration labels.

**At publish**, each procedure's rules in the release that was live are compared with the new release's. A rule whose id is in both continues. Changed rules are matched old to new, one to one, within one procedure and one kind (step, never rule, warning sign), in this order:

1. `suggestion`: an applied suggestion (a draft, or a pull request open or merged) whose edit is in the file published (its suggested file, or that merged with other edits) knows the rule it edited. Its new wording is the new rule at that position, else the most similar new one.
2. `matched`: the new rule at the same position, if the two wordings' similarity is at least 0.35.
3. `matched`: the most similar remaining pair anywhere in the procedure, best first, if at least 0.5 (moved and reworded).

Similarity is token Jaccard over lowercase words, without a few function words (`a`, `the`, `to`, `you`...). On the test fixture's and the walkthrough's procedures, rewordings score 0.38 to 1, and two different rules of one procedure score at most 0.29.

An unmatched new rule is new, and an unmatched old one is removed. A wording that comes back (a revert) stays in its own lineage. Links are never overwritten by later publishes.

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/rules/{rule_id}/lineage?procedure=` | `{procedure_id, rule_id, lineage_id, previous_rule_id, how, rules: [{rule_id, previous_rule_id, how, similarity, release_id, text, from_release}]}`, oldest first. Pass `procedure` only when the id is in more than one procedure. |
| `POST /v1/workspaces/{ws}/rules/{rule_id}/lineage` | `{previous_rule_id, procedure_id?}`: the rule replaced `previous_rule_id` (a `manual` link). See below. `409` for a cycle. |
| `DELETE /v1/workspaces/{ws}/rules/{rule_id}/lineage?procedure=` | The rule is a new rule, kept as a `manual` row with no previous rule. Its findings, and the later wordings', move to a rule pattern of their own, with no verdict. |
| `POST /v1/workspaces/{ws}/rules/lineage/rebuild` | Recompute the `matched` and `suggestion` links of every release against the one before it by number (manual rows stay), and move patterns to match. Idempotent. Returns `{workspace, links, procedures}`. |

**Linking a rule by hand** (`POST .../lineage`) moves its findings into the earlier rule's pattern. When both had a pattern they merge into the older one: failure modes, verdicts (whose latest then counts) and suggestions move over. If the older one is split, the findings wait in `other` for the next recluster.

`sopops lineage rebuild [--workspace WS]` does the rebuild from the command line. Migration 0013 ran it on every workspace when it added the table; it needs the sopc binary, and without one it says to run the command.
