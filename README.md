<div align="center">

# 📋🗄️ sopops

**Manage your agents' instructions, serve them at call start, and QA every session against them.**

Edit shared instructions and procedures in a browser, compose each agent from them, and publish releases<br>
your agents fetch when a call starts. Every session comes back, is checked rule by rule, and what keeps<br>
going wrong is grouped into patterns you review and fix with a suggested edit.

[Concepts](https://github.com/amanmibra/whatsop) · [Quickstart](#quickstart) · [Walkthrough](examples/walkthrough/WALKTHROUGH.md) · [Import your agents](#import-your-agents) · [Connect your agent](#connect-your-agent) · [Sessions](#sessions-and-checks) · [Review](#patterns-review-and-suggestions) · [Models](#models) · [Configuration](#configuration) · [API](#api)

</div>

---

## Why

[sopc](https://github.com/amanmibra/sopc) keeps agent instructions in git and compiles one prompt per agent. sopops is the service around it: forms to edit blocks and compose agents, drafts and releases, an API each agent calls when it starts, and QA on what the agents then do. It compiles with the sopc binary itself, so its prompts match the CLI byte for byte.

The loop:

1. **Manage.** Shared instructions, procedures (SOPs) and agents, edited in the UI or kept in git. Every save is a draft; a release is an immutable snapshot.
2. **Serve.** Agents fetch their prompt when a call starts (or sopops pushes it to the platform). A publish or a rollback reaches the next call, with no redeploy.
3. **Check.** Each session comes back with the release it ran on and is checked against every procedure that applied: each step, never rule and warning sign, with the quotes that show it.
4. **Cluster.** Findings on a rule become a pattern, split into named failure modes once there are enough, grouped into themes by the phase of the call.
5. **Review.** A verdict per pattern: a real mistake, acceptable, the SOP is wrong, or sopops is wrong. Acceptable and misjudged findings stop counting.
6. **Suggest.** A verdict that calls for an edit gets one: a procedure change compiled with sopc, with each agent's prompt diff, applied as a draft or opened as a GitHub pull request. Insights compares releases, so you see whether it helped.

<p align="center"><img src="docs/images/agent-builder.png" alt="The agent builder: the agent's own text, its blocks as an ordered stack of cards, its variables, and the draft prompt with a diff against the live release" width="900"></p>

## Quickstart

With Docker, from a clone:

```sh
docker compose up --build      # sopops and Postgres, on http://127.0.0.1:8484
```

Or just the image: `docker build -t sopops . && docker run -p 127.0.0.1:8484:8484 sopops` (SQLite in its `/data` volume). Or from a clone, with [sopc](https://github.com/amanmibra/sopc) v0.0.17 or later:

```sh
uv sync && pnpm -C web install && pnpm -C web build
SOPC_BIN=/path/to/sopc uv run sopops       # http://127.0.0.1:8484, SQLite in ./sopops.db
```

Open http://127.0.0.1:8484, create a workspace, and import your agents or add a shared instruction, a procedure and an agent. The switch in the top bar has two modes: **QA** (Overview, Sessions, Insights, Patterns, Suggestions, Report) and **Instructions** (agents, shared instructions, procedures, groups, Changes, Releases, Import); the gear icon opens the workspace's settings. The API docs are at `/docs`. To check sessions, set a model ([Models](#models)).

**See the whole loop first:** [the walkthrough](examples/walkthrough/WALKTHROUGH.md) onboards a sample LiveKit agent repo, sends 300 synthetic calls with planted failures, reviews the patterns, publishes the fix and checks the result against the ground truth. `just examples/walkthrough/all` runs it in about a minute with fake models (no keys, no cost), or with yours.

## How it works

| | What it is |
|---|---|
| Shared instruction | Prompt text several agents get (identity, voice, policy). |
| Procedure | An SOP: goal, when it applies, steps (with tools), never rules, warning signs. |
| Group | A named, ordered list of blocks. An agent lists it like a block. |
| Agent | One per voice agent: platform id, its own text, its blocks in order, variables. |
| Release | What agents are served: every block plus the compiled prompts. Pin one per call for A/B tests. |

Before you publish, **Changes** (in Instructions) shows what was edited and exactly how each agent's prompt changes, plus any duplicated or conflicting instructions `sopc lint` finds. Roll back by making an older release live. The **Overview** shows every agent against every block:

<p align="center"><img src="docs/images/overview.png" alt="Overview: a grid of agents against groups, shared instructions and procedures, with checkmarks, including blocks that come through a group" width="860"></p>

## Import your agents

Already running agents? **Import** turns their prompts into sopops: each agent's own text, shared instructions, procedures, and `{{variables}}` where agents differ only by a value. Where they disagree on a rule you pick one or keep both. Every agent is compiled and compared with its original sentence by sentence, so nothing is dropped silently. Applying writes drafts; you publish as usual.

**With Claude Code or Codex (recommended).** The Import page gives you a 2-hour onboarding token (this workspace only; it can send progress and a proposal, nothing else) and one command to run in your agents' repo:

```sh
claude mcp add sopops --env SOPOPS_URL=http://127.0.0.1:8484 --env SOPOPS_TOKEN=sopops_onb_... \
  -- uvx --from git+https://github.com/amanmibra/sopops@main sopops-mcp \
  && claude "Onboard this project's agents into sopops: call the sopops get_kickoff tool and follow it."
```

(Codex: the same with `codex mcp add` and `codex "…"`.) Your agent finds the instructions wherever they live (code, prompt files, config, a database, ElevenLabs or Vapi), extracts them with the `sopops-mcp` tools, checks fidelity and pushes the proposal, while you watch on the import page. **In my repo too** (repo mode) connects GitHub right there, with the same form as Settings, and sopops looks in the folder (default `sops/`) on the base branch: a sopc project sopc builds is offered for import as it is; with none, the coding agent also opens the onboarding pull request adding it (nothing merges without you), reports it, and sopops follows it and pulls the folder into drafts when you merge it; one sopc can't build shows sopc's errors, to import it anyway and fix it, or have the agent rewrite it in a pull request. **In sopops** can connect GitHub too (optional), so suggested fixes arrive as pull requests. The instructions are [skills/sopops-onboard/SKILL.md](skills/sopops-onboard/SKILL.md); `sopops-mcp install-skill` copies it into the repo (`--claude`, `--codex`), to start with `claude "/sopops-onboard"` or `codex '$sopops-onboard'`. `SOPOPS_MCP_SOURCE` on the server changes what `--from` installs (e.g. a local clone).

**An existing sopc project.** Already keep one, or export one from wherever your prompts live? Import → **A sopc project**: pick its folder (or read it from the connected GitHub repository) and add each agent's original prompt. sopops runs the same fidelity check (sentences kept, rules word for word, similarity, missing lines) and applying writes exactly the project's files as drafts (`POST …/imports/sopc`, [API.md](API.md#onboarding)). The [walkthrough](examples/walkthrough/WALKTHROUGH.md#2-onboard-the-agent-repo) shows both ways.

**Paste, upload, ElevenLabs or Vapi.** sopops extracts with its own model (`SOPOPS_MODEL_REASONING`; `rules` tries it without a model). Platform keys are used for that request only, never stored or logged.

## Connect your agent

Each agent gets its prompt one of two ways, set per agent under the gear icon → Delivery:

- **Pull:** the agent, or its platform's call-start webhook, fetches the live release when a call starts.
- **Push:** publishing, or making a release live, writes the prompt into the agent on its platform, with results and retry on the release.

| Platform | Prompt | Sessions back |
|---|---|---|
| **LiveKit** | Pull with the Python SDK's `attach` (or `fetchInstructions` in TypeScript) | `attach` posts `session.history` at shutdown, and can stream it live; `recordSession` in TypeScript |
| **ElevenLabs** | Push (`conversation_config.agent.prompt.prompt`), the conversation-initiation webhook for phone calls, or client overrides from the TypeScript SDK | Posted (and optionally streamed) by the TypeScript SDK's `elevenLabsSession`; phone calls: send transcripts in the canonical format |
| **Vapi** | Push (the system message in `model.messages`), or the assistant-request webhook | Send transcripts in the canonical format |
| **Retell** | Later: agents can be marked Retell, nothing is delivered yet | |
| **Anything else** | `GET …/agents/<agent>` with a runtime token, or either SDK | `POST …/sessions`, or `sopops import FILE.jsonl` |

**Python** (`pip install "./sdks/python[livekit]"`, [sdks/python](sdks/python); `SOPOPS_URL` is the workspace's URL, `http://sopops:8484/v1/workspaces/<ws>`, and `SOPOPS_TOKEN` a token with the `runtime` and `ingest` scopes):

```python
from sopops_client.livekit import attach

async def entrypoint(ctx: JobContext):
    await ctx.connect()
    session = AgentSession(...)
    ins = await attach(ctx, session, agent="livekit:tonys-pizza", fallback=FALLBACK, stream=True)
    await session.start(room=ctx.room, agent=Agent(instructions=ins.text, tools=TOOLS))
```

`attach` fetches the instructions (a release pinned as `{"sopops_release": 3}` in job or room metadata wins), notes the release in `session.userdata`, streams turns and tool calls while the call runs (`stream=True`, optional), and posts the full session at shutdown. If sopops is down it uses the last good copy on disk, then `fallback`, so it never blocks a call. Elsewhere: `Sopops().fetch_instructions(agent)` and `post_session(...)`. A complete agent: [examples/livekit-restaurant](examples/livekit-restaurant).

**TypeScript** ([sdks/node](sdks/node); ESM, no dependencies, Node 18+, browsers and edge runtimes):

```ts
import { Sopops } from "sopops-client";
import { elevenLabsSession } from "sopops-client/elevenlabs";

const sopops = new Sopops({ url: SOPOPS_URL, token: SOPOPS_TOKEN });
const s = await elevenLabsSession(sopops, { agent: "front-desk", stream: true });
const conversation = await Conversation.startSession({ ...s.sessionOptions });

const ins = await sopops.fetchInstructions("front-desk", { fallback: "You are a helpful assistant." });   // any framework
```

**Webhooks.** ElevenLabs phone calls: the conversation-initiation webhook at `…/runtime/elevenlabs/conversation-init`, with the webhook secret as `X-Sopops-Secret` (and the agent's system-prompt override allowed). Vapi: the phone number's Server URL at `…/runtime/vapi/assistant-request?agent=<id>`, with the secret as a Bearer Token credential.

**Tokens.** The gear icon → Tokens, each with the scopes it needs: `runtime` (prompt fetches, call-start webhooks) and `ingest` (sessions). Shown once, stored hashed, can expire or be revoked. Once a workspace has a runtime token, prompt fetches need one; without one it serves prompts openly, for local use. Platform keys and webhook secrets are encrypted with `SOPOPS_SECRET_KEY` and never returned.

## Sessions and checks

A session is one conversation: a voice call or a text chat. Every source converts into one canonical JSON format ([API.md](API.md#sessions)), sent to `POST …/sessions` with an `ingest` token, or streamed while it runs (`…/sessions/open`, `…/events`, `…/close`; a session idle for `SOPOPS_SESSION_IDLE_MINUTES` closes as incomplete). Sending the same session again is safe: it's keyed by platform and session id. `sopops import sessions.jsonl --workspace prod` reads a backfill straight into the database ([examples/import-sessions](examples/import-sessions)).

The **Sessions** page lists them live, each with its transcript, tool calls, and how it went against each procedure, with the quotes highlighted. Each new or changed session runs the pipeline:

1. **normalize** the turns and tool calls;
2. **route**: which of the agent's procedures, in the release it ran on, applied, and which ended before they started or finished;
3. **check** each procedure that applied: a decision per rule, the reviewer model on what they flag, a status per rule with quotes, the goal; required tool steps against the tool calls;
4. a **result** per procedure and per session: deviated, partial, followed or not measurable ([how](API.md#sop-checks));
5. **patterns**: each finding joins its rule's pattern ([below](#patterns-review-and-suggestions)).

Without Hatchet it runs inside sopops, after the response. With `HATCHET_CLIENT_TOKEN` it runs on [Hatchet](https://hatchet.run) in `sopops worker`: one run per session version, a fair share per workspace, model calls rate-limited per model, and a daily recluster. `docker compose --profile hatchet up` runs hatchet-lite ([docker-compose.yml](docker-compose.yml)). Live updates reach the UI from any process on Postgres (LISTEN/NOTIFY); on SQLite, from the sopops process only.

**Insights** shows sessions checked, followed end to end and needing attention over a window, against the prior one; a trend with release markers; a scoreboard per procedure, its rule ladder and each rule's page; and **Release compare**, two releases side by side, rule by rule, with 95% intervals. Every number links to its sessions ([definitions](API.md#insights)). The **Report** reads like a hand-written call review.

## Patterns, review and suggestions

**Patterns.** Every rule with findings is a pattern, named by the reviewer model for what the agent did wrong ("Didn't read the order back"; without a model, the rule's own words). Once a rule has 8, the reviewer model splits it into named failure modes ("Told the caller a dish is safe"), new findings are assigned to them, and themes group them by the phase of the call. Reclustering keeps names and ids stable ([how](API.md#patterns-and-themes)).

**Review.** QA → Patterns → Needs review is a queue (keys `1` to `4`) of patterns waiting for a verdict:

| Verdict | Means | Then |
|---|---|---|
| Real mistake | The agent did something wrong. | Findings keep counting; a suggested edit to the instructions. |
| Acceptable | Fine in this situation. | Findings suppressed: kept, but out of every Insights number; a suggested exception to the rule. |
| SOP is wrong | The instruction is outdated. | A suggested rewrite of the rule. |
| Checker is wrong | sopops misjudged. | Findings suppressed, and kept as calibration labels. |

A single finding can be marked wrong too. A pattern with a verdict exports its sessions as regression cases (JSON Lines).

**Reworded rules.** sopc gives a rule a new id when its text changes, and every applied suggestion rewords one. sopops records which rule each reworded rule replaced when you publish (from the suggestion that made the edit, else by matching the wording at the same position, then anywhere in the procedure), so the rule keeps its pattern, verdicts, review state and Insights history across wordings, while each session keeps the exact wording it ran. Insights marks a reworded rule ("reworded in release 4", the earlier wordings on hover). When the matcher is wrong, link or unlink the two rules by hand on the rule's Insights page ([API.md](API.md#rule-lineage)).

Each pattern then moves through review states: **needs review** (no verdict, or a recluster changed its findings since), **open** (a real mistake or a wrong SOP, not fixed yet), **fixing** (a suggestion applied or its pull request open or merged, waiting for the release to go live and for 10 sessions on it), **fixed**, or **regressed** (it came back on sessions that ran the fix, so it's back in the queue), and **closed** for acceptable and checker-wrong. Pull requests are followed on GitHub: merged moves the pattern on, closed reopens it.

**Suggestions.** The reviewer model proposes the smallest edit to the procedure that settles the verdict; sopops compiles it with sopc (one retry if it doesn't) and shows each agent's prompt diff. Apply saves it as a draft, or opens a pull request when the workspace has a GitHub repository connected. When the procedure changed after a suggestion was written (another one was applied, or someone edited it), the edit is merged into the file as it is now if the two touch different lines and the result compiles; if they overlap, the suggestion shows the conflicting lines and can't be applied, and **Rewrite against the current text** writes it again from the file as it is, keeping the edits made since. Details: [API.md](API.md#review-and-suggestions).

## Models

Every model call is a typed [DSPy](https://dspy.ai) signature ([`src/sopops/ai/signatures.py`](src/sopops/ai/signatures.py)), and every result records the model and instructions version that made it. Bring your own keys.

- **Two roles.** `SOPOPS_MODEL_FAST` answers the decisions (routing, rule checks, pattern assignment) and `SOPOPS_MODEL_REASONING` is the reviewer: it judges the rules the decisions flag or aren't sure about, splits rules into checks, names patterns, clusters, names themes and writes suggestions. Model strings like `anthropic/claude-haiku-4-5` and `anthropic/claude-sonnet-5-5`, with the provider's key in its usual variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...); either alone serves both. Any provider DSPy knows works, or an OpenAI-compatible endpoint for local models (`SOPOPS_LLM_API_BASE`). Unset, sessions are stored but not checked.
- **Jev (optional).** With `pip install 'sopops[jev]'` and a `TYPESAFE_API_KEY`, TypeSafe's [Jev](https://docs.typesafe.ai) answers the decisions instead of the fast model: calibrated probabilities, faster and much cheaper than an LLM call. The reviewer still reviews.
- **Check modes.** `decide` (default): decisions first, the reviewer on what they flag. `review`: the reviewer judges every rule. `SOPOPS_CHECK_REVIEW=off` lets the decisions stand.
- **Spend.** Every model call is recorded with its tokens and an estimated cost (a price table you can override with `SOPOPS_MODEL_PRICES`), by purpose and session: QA → Insights → Model spend, and each session's page ([API.md](API.md#model-spend)). Prompts aren't stored.
- **Calibration.** Decision thresholds can be fitted to a workspace's own labeled sessions (verdicts and overrides are the labels; checker-wrong ones are negative) and are stored per workspace; until then the defaults apply (`SOPOPS_DECISIONS` overrides them). Run it with `sopops calibrate --workspace WS [--procedure P] [--dry-run]` or `POST …/calibrate`: it reports each threshold before and after, and refuses a procedure with fewer than 5 labeled sessions or no rule with 3 labeled failures. Calibration widens the review band, never narrows it.

## Configuration

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | `sqlite:///sopops.db` | Postgres (`postgresql://`, with `pip install 'sopops[postgres]'`) or SQLite. Migrated on startup (Alembic). |
| `SOPC_BIN` | `sopc` on `PATH` | The sopc binary, v0.0.17 or later. |
| `HOST`, `PORT` | `127.0.0.1`, `8484` | Also `--host`, `--port`. |
| `SOPOPS_MODEL_FAST`, `SOPOPS_MODEL_REASONING` | unset | The two model roles ([Models](#models)); `SOPOPS_MODEL_REASONING=rules` tries importing without a model. |
| `SOPOPS_LLM_API_BASE`, `SOPOPS_LLM_TIMEOUT`, `SOPOPS_MAX_TOKENS` | unset, `300`, `16000` | An OpenAI-compatible base URL for every model call; per-call timeout (s) and output limit. |
| `TYPESAFE_API_KEY`, `SOPOPS_JEV`, `SOPOPS_JEV_MODEL` | unset, auto, `jev-1.13.0` | Jev for decisions: on when the key is set, unless `SOPOPS_JEV=off`. |
| `SOPOPS_CHECK_MODE`, `SOPOPS_CHECK_REVIEW`, `SOPOPS_DECISIONS` | `decide`, `on`, unset | How rules are checked; reviewer on or off; decision thresholds and bands as JSON, e.g. `{"never": [0.05, 0.7, 0.7]}`. |
| `SOPOPS_MODEL_PRICES` | unset | Model prices for spend estimates, JSON in USD per million tokens: `{"openai/gpt-5-mini": [0.25, 2]}`; adds to or overrides the built-in table (Jev, current Claude models). |
| `SOPOPS_DSPY_CACHE_DIR` | unset | A disk cache for model responses (transcripts are written there); in memory otherwise. |
| `SOPOPS_SECRET_KEY` | unset | A Fernet key that encrypts platform keys, webhook secrets and GitHub tokens; needed to store them. Make one: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `SOPOPS_REQUIRE_WEBHOOK_SECRET` | `false` | Set `true` when sopops is hosted: call-start webhooks are refused for a workspace with no webhook secret or runtime token, instead of answered openly. |
| `SOPOPS_CORS_ORIGINS` | unset | Comma-separated origins allowed to call sopops from a browser. |
| `SOPOPS_SESSION_IDLE_MINUTES` | `30` | An open (streamed) session with no events this long closes as incomplete. |
| `SOPOPS_MCP_SOURCE` | `git+https://github.com/amanmibra/sopops@main` | What the onboarding command's `uvx --from` installs. |
| `SOPOPS_GITHUB_API_URL` | `https://api.github.com` | GitHub Enterprise Server: `https://HOST/api/v3`. |
| `HATCHET_CLIENT_TOKEN` | unset | Run the pipeline on Hatchet (`pip install 'sopops[hatchet]'`, plus `sopops worker`). |
| `SOPOPS_RATE_LIMIT_PER_MINUTE`, `SOPOPS_WORKSPACE_CONCURRENCY`, `SOPOPS_WORKER_SLOTS`, `SOPOPS_RECLUSTER_CRON`, `SOPOPS_PR_STATUS_CRON` | `60`, `4`, `20`, `0 6 * * *`, `*/15 * * * *` | Hatchet only: model calls per minute per model, runs per workspace at once, worker slots, the daily recluster, checking suggestions' and onboarding's pull requests (without Hatchet, run `sopops recluster` and `sopops pr-status` from cron). |
| `SOPOPS_WEB_DIR` | the built UI | Serve the UI from another folder. |

## Git or database

**Database.** Everything lives in sopops's database and is edited in the UI or over the `/config` API. Nobody edits YAML or Markdown, but each block is still stored as a sopc file and versioned on its own.

**Git.** The sopc folder lives in your repo, and CI publishes it on merge in one call:

```sh
python3 -c 'import json,pathlib; r=pathlib.Path("sops"); print(json.dumps({"files": {p.relative_to(r).as_posix(): p.read_text() for p in r.rglob("*") if p.is_file()}}))' \
  | curl -fsS -X POST -H 'Content-Type: application/json' -d @- "$SOPOPS_URL/publish"
```

`build/` is ignored. A folder in the format of sopc v0.0.8 or earlier, or one that still sets `locked` (sopc v0.0.9), is refused with a message to run `sopc migrate`; a workspace stored that way shows a Convert page in the UI.

You can switch any time: `GET …/files?content=true` exports a workspace as sopc files to commit.

### Connect a GitHub repository

Instead of CI, sopops can read the repo itself, and send changes back as pull requests. Connect it under the gear icon (Settings), or with `POST …/repo`: give the repository, the branch (default `main`), the sopc project's folder (default `sops`, where onboarding puts it) and a [fine-grained personal access token](https://github.com/settings/personal-access-tokens/new) for that one repository with these permissions:

| Permission | Access | For |
|---|---|---|
| Contents | Read and write | Reading the project; committing a suggestion to its own branch |
| Pull requests | Read and write | Opening the pull request, and following whether it's merged |
| Metadata | Read | Required by GitHub for every fine-grained token |

sopops checks that the token can read the repository and that the folder has a `sopc.yaml` (onboarding connects before there is one, and proposes it in a pull request), then stores the token encrypted with `SOPOPS_SECRET_KEY`; the API never returns it. **Pull** (`POST …/repo/pull`) makes the workspace's drafts exactly the folder at the branch's head (files sopops has that the repo doesn't are deleted, `build/` is skipped) and records the commit; you review and publish as usual, and pulling the same commit again does nothing. **Pull requests come from suggestions**: an instruction edit made from reviewed patterns is committed to a new `sopops/<id>` branch off the branch's head and opened as a PR against it, so the change goes through your usual review. sopops never pushes to the base branch. For GitHub Enterprise Server, set `SOPOPS_GITHUB_API_URL` to `https://HOST/api/v3`.

## API

Paths are under `/v1/workspaces/<ws>`. The full spec is at `/openapi.json` (SDKs generate from it), and [API.md](API.md) lists every endpoint.

| Endpoint | What it does |
|---|---|
| `GET …/agents/<agent>` | **Call start.** The prompt, release, hash, tools and the components it was built from. `?release=N` pins a release. |
| `GET …/agents/<agent>/prompt` | Just the prompt text. |
| `…/runtime/delivery`, `…/runtime/platforms`, `POST …/runtime/served` | Each agent's delivery mode; platform keys and webhook secrets (write-only); which release a platform call ran on. |
| `GET …/releases/<n>/pushes`, `POST …/pushes/retry` | What publishing pushed to ElevenLabs and Vapi; push again. |
| `POST …/runtime/elevenlabs/conversation-init`, `…/runtime/vapi/assistant-request` | Call-start webhooks that serve the prompt. |
| `POST …/sessions`; `…/sessions/open`, `…/sessions/<platform>/<id>/events`, `…/close` | **Session end**, with an `ingest` token; or streamed while it runs. |
| `GET …/sessions`, `…/sessions/<id>`, `…/sessions/stream` | List sessions (filters, cursor), one with its transcript and checks, and live events (SSE). |
| `GET …/patterns`, `…/patterns/<id>`, `POST …/patterns/recluster` | Patterns with their failure modes; recluster now. |
| `GET …/review`, `POST …/patterns/<id>/verdict`, `POST …/findings/<id>/override`, `GET …/patterns/<id>/regression` | The review queue by review state, verdicts, one finding marked right or wrong, regression cases. |
| `POST …/calibrate` | Fit the check thresholds to the workspace's review labels (or a dry run). |
| `GET/POST/DELETE …/rules/<rule_id>/lineage`, `POST …/rules/lineage/rebuild` | Which rule a reworded rule replaced; link or unlink two rules by hand; recompute from the releases. |
| `GET …/suggestions[/<id>]`, `POST …/patterns/<id>/suggest`, `POST …/suggestions/<id>/apply`, `…/dismiss` | Suggested edits: list, (re)generate, apply as a draft or pull request, dismiss. |
| `GET …/insights/overview`, `…/procedures/<id>[/rules/<id>]`, `…/releases/compare`, `…/report` | Insights and the report. |
| `GET/POST …/tokens`, `DELETE …/tokens/<id>` | Workspace tokens with scopes (`ingest`, `runtime`), shown once; revoke. |
| `GET …/config`, `…/config/{agents,instructions,procedures,groups}[/<id>]` | Every item as the forms edit it; list, create, read, replace and delete each kind, with history. |
| `GET …/draft`, `POST …/releases`, `POST …/releases/<n>/activate` | Unpublished changes; publish head; make an older release live. |
| `POST …/publish`, `GET …/files?content=true` | Replace every file and release, for CI; export as sopc files. |
| `GET/POST/DELETE …/repo`, `POST …/repo/pull` | The workspace's GitHub repository (token write-only; `null` when none); pull its sopc project into drafts. |
| `…/imports`, `/v1/onboarding/*` | Import existing prompts: extraction jobs, onboarding tokens for coding agents, review and apply. |

Errors from sopc come back as `422 {"valid": false, "issues": [...]}`, each issue naming the item and field it's about, in plain words.

## Status

v0. **There is no authentication yet** except workspace tokens, which guard session ingest, onboarding pushes and, once the workspace has a runtime token, prompt fetches. Anyone who can reach sopops can read and change everything else, so run it on a private network only.

| Built | Planned |
|---|---|
| Blocks, agents, drafts, releases, rollback; sopc-exact compiling; git or database | Sign-in for the UI and tokens for the rest of the API (hosted auth) |
| Import with a coding agent (sopops-mcp) or sopops's own model | Experiments: A/B tests as a feature (today: pin a release per call and compare releases) |
| Pull and push delivery: LiveKit, ElevenLabs, Vapi; Python and TypeScript SDKs | Retell delivery; ElevenLabs and Vapi post-call webhooks; an OpenTelemetry receiver |
| Sessions: ingest, streaming, backfill import; checks per rule with quotes | Runtime enforcement: checking rules while a session is still going, and guidance back to the agent (the SDKs' `on_guidance` is reserved for it) |
| Patterns, themes, review verdicts and states, suppression, calibration from review labels, regression export; all of it following a rule across its wordings | A Slack digest of new patterns and suggestions |
| Suggested edits as drafts or GitHub pull requests; pulling a repo's sopc project | A GitHub App instead of a personal token; pulling on push |
| Insights, release compare, the report | Renaming agents and blocks; a webhook on publish; more framework adapters (Pipecat) |

## Development

The backend is FastAPI with SQLModel and Alembic (`src/sopops`); the UI is Vite, React, TanStack Router and Query, Tailwind and shadcn/ui (`web/`). In production sopops serves the built UI itself, one process.

| Path | |
|---|---|
| `src/sopops/api/` | HTTP routers, one per area; `app.py` mounts them |
| `src/sopops/schemas/` | API request and response shapes, one module per area |
| `src/sopops/orm/` | Database tables, one module per table; migrations in `migrations/` |
| `src/sopops/ai/` | Model calls: `signatures` (every DSPy signature), `lm` (models per role), `decide` (decisions and review), `programs` and `calibration` (fitted thresholds), `fakes` (for tests) |
| `src/sopops/pipeline/` | The session pipeline: normalize, route, check, rollup |
| `src/sopops/onboarding/`, `runtime/`, `jobs/` | Importing existing agents; delivering prompts to platforms; inline or Hatchet runs |
| `src/sopops/store.py`, `sessions.py`, `checks.py`, `patterns.py`, `verdicts.py`, `suggestions.py`, `lineage.py`, `calibrate.py`, `insights.py`, `github.py`, `tokens.py` | The data layer and services, by area |
| `src/sopops/cli.py`, `mcp_server.py` | `sopops` (serve, import, worker, recluster, calibrate, pr-status, lineage rebuild, patterns rename) and `sopops-mcp` |
| `web/` | The UI; `web/src/client` is generated from the OpenAPI spec |
| `sdks/python`, `sdks/node` | The client SDKs |
| `skills/sopops-onboard` | The onboarding skill coding agents follow |
| `examples/` | [walkthrough](examples/walkthrough/WALKTHROUGH.md), [livekit-restaurant](examples/livekit-restaurant), [elevenlabs-web](examples/elevenlabs-web), [import-sessions](examples/import-sessions) |

```sh
uv sync --all-extras
SOPC_BIN=/path/to/sopc uv run sopops        # the API on :8484 (and the UI, once built)
pnpm -C web install && pnpm -C web dev       # the UI on :5173, proxying /v1 to :8484
```

| | |
|---|---|
| `cd sdks/python && uv run pytest`, `pnpm -C sdks/node test` | The client SDKs' tests. |
| `uv run pytest` | Backend tests, on SQLite. `SOPOPS_TEST_DATABASE_URL=postgresql://…` runs them on Postgres. Hatchet isn't needed. |
| `uv run sopops import FILE.jsonl -w WS` | Import sessions. |
| `uv run sopops worker` | The Hatchet worker, with `HATCHET_CLIENT_TOKEN` set. |
| `uv run sopops-mcp` | The onboarding MCP server (stdio), as Claude Code or Codex runs it. |
| `pnpm -C web build` | Typecheck and build the UI into `web/dist`, which `sopops` serves. |
| `pnpm -C web gen:api` | Regenerate the typed client (`web/src/client`) from the API's OpenAPI spec. Commit it with the API change. |
| `uv run alembic revision --autogenerate -m "…"` | A new migration after changing a table in `orm/`. |

## License

Apache-2.0
