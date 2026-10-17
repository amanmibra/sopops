<div align="center">

# 📋🗄️ sopops

**Serves sopc agent prompts from a UI and an API, collects your agents' sessions, and checks each one against its procedures. Patterns across sessions and GitHub PRs are coming.**

Edit shared instructions and procedures in a browser, compose each agent from them,<br>
and publish releases your agents fetch when a call starts. No redeploys.

[Concepts](https://github.com/amanmibra/whatsop) · [Why](#why) · [Quickstart](#quickstart) · [How it works](#how-it-works) · [Import your agents](#import-your-agents) · [Connect your agent](#connect-your-agent) · [Sessions](#sessions) · [Insights](#insights-and-the-report) · [Git or database](#git-or-database) · [API](#api)

</div>

---

## Why

[sopc](https://github.com/amanmibra/sopc) keeps agent instructions in git and compiles one prompt per agent. Some teams would rather keep that config in a database, edit it in a UI, and not trigger a deploy for every wording change. sopops is that: forms to edit blocks and compose agents, drafts and releases, and an API each agent calls when it starts. It compiles with the sopc binary itself, so its prompts match the CLI byte for byte.

<p align="center"><img src="docs/images/agent-builder.png" alt="The agent builder: the agent's own text, its blocks as an ordered stack of cards, its variables, and the draft prompt with a diff against the live release" width="900"></p>

## Quickstart

With Docker, from a clone:

```sh
docker compose up --build      # sopops and Postgres, on http://127.0.0.1:8484
```

Or just the image: `docker build -t sopops . && docker run -p 127.0.0.1:8484:8484 sopops` (SQLite in its `/data` volume). Set `DATABASE_URL` to a Postgres URL in production (Supabase, Neon and RDS all work).

Open http://127.0.0.1:8484, create a workspace, and add a shared instruction, a procedure and an agent. The API docs are at `/docs`.

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | `sqlite:///sopops.db` | Postgres (`postgres://` URLs use psycopg 3, `pip install 'sopops[postgres]'`) or SQLite. |
| `SOPC_BIN` | `sopc` on `PATH` | The sopc binary: v0.0.12 or later. |
| `HOST`, `PORT` | `127.0.0.1`, `8484` | Also `--host`, `--port`. |
| `SOPOPS_MODEL_FAST`, `SOPOPS_MODEL_REASONING` | unset | The models that check sessions and import prompts, as [LiteLLM](https://docs.litellm.ai/docs/providers) model strings (e.g. `anthropic/claude-haiku-4-5` and `anthropic/claude-sonnet-5-5`), with the provider's key in its usual variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...). If only one is set it serves both. Unset, sessions are stored but not checked, and imports need Claude Code or Codex. `SOPOPS_MODEL_REASONING=rules` tries importing without a model. |
| `SOPOPS_LLM_API_BASE` | unset | Send every model call to this base URL (an OpenAI-compatible endpoint for local models). |
| `SOPOPS_SECRET_KEY` | unset | A Fernet key that encrypts platform API keys and webhook secrets. Needed to store them. Make one: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `SOPOPS_CORS_ORIGINS` | unset | Comma-separated origins allowed to call sopops from a browser. |
| `SOPOPS_REQUIRE_WEBHOOK_SECRET` | `false` | Set `true` when sopops is hosted: call-start webhooks (ElevenLabs, Vapi) are refused for a workspace with no webhook secret or runtime token, instead of open. |
| `HATCHET_CLIENT_TOKEN` | unset | Run the session pipeline on [Hatchet](https://hatchet.run) (`pip install 'sopops[hatchet]'`, plus `sopops worker`). Unset, it runs inside sopops. |

The schema is managed with Alembic and migrated on startup. A database from an older sopops is picked up as is.

## How it works

1. **Write blocks.** Shared instructions are text several agents get (identity, voice, policy). Procedures are step-by-step SOPs. A block doesn't say who uses it.
2. **Compose agents.** Each agent is its own text, then the blocks it lists, in prompt order. Groups name a set of blocks many agents share.
3. **Review the diff.** Every save is a draft. Unpublished changes shows what changed in each block and in each agent's prompt, plus any duplicated or conflicting instructions `sopc lint` finds (advisory).
4. **Publish a release.** A release is an immutable snapshot of every block plus the compiled prompts.
5. **Agents fetch their prompt at call start**, with the release and block versions it was built from.
6. **Roll back anytime** by making an older release live.

| | What it is |
|---|---|
| Shared instruction | Prompt text that isn't a procedure. |
| Procedure | Goal, when it applies, steps (with tools), never-do's, warning signs. |
| Group | A named, ordered list of blocks. An agent lists it like a block. |
| Agent | One per voice agent: platform id, its own text, its blocks in order, variables. |
| Release | What agents are served. Pin one per call for A/B tests. |

The Overview page shows every agent against every block, and a click adds or removes one:

<p align="center"><img src="docs/images/overview.png" alt="Overview: a grid of agents against groups, shared instructions and procedures, with checkmarks, including blocks that come through a group" width="860"></p>

Before you publish, Unpublished changes shows what was edited and exactly how each agent's prompt changes:

<p align="center"><img src="docs/images/changes.png" alt="Unpublished changes: one block added to tonys-pizza's list, and the resulting prompt diff with the new Reservations procedure" width="860"></p>

## Import your agents

Already running agents? **Import** (in the sidebar, on an empty workspace, or in Settings) turns their prompts into sopops: each agent's own text, shared instructions, procedures, and `{{variables}}` where agents differ only by a value. Where they disagree on a rule ("2 agents differ on the upsell rule"), you pick one or keep both. Every agent is compiled and compared with its original sentence by sentence, so nothing is dropped silently. Applying writes drafts; you publish from Unpublished changes as usual.

**With Claude Code or Codex (recommended).** sopops gives you a 2-hour onboarding token (this workspace only; it can send progress and a proposal, nothing else), one command and a prompt to paste:

```sh
claude mcp add sopops --env SOPOPS_URL=http://127.0.0.1:8484 --env SOPOPS_TOKEN=sopops_onb_... \
  -- uvx --from git+https://github.com/amanmibra/sopops@v0 sopops-mcp
```

Your agent finds the instructions wherever they live (code, prompt files, config, a database), extracts them with the `sopops-mcp` tools, checks fidelity, and pushes the result, while you watch on the import page. In repo mode it also opens a PR adding a sopc project at `sops/`. See [skills/sopops-onboard](skills/sopops-onboard/SKILL.md) for the tools and Cursor setup.

**Paste, upload, ElevenLabs or Vapi.** sopops extracts with its own model (`SOPOPS_MODEL_REASONING`). ElevenLabs and Vapi keys are used to read the agents during that request and are never stored or logged.

## Connect your agent

Each agent gets its prompt one of two ways, set per agent in Settings → Delivery:

- **Pull:** the agent, or its platform's call-start webhook, fetches the live release when a call starts. A rollback reaches the next call.
- **Push:** publishing, or making a release live, writes the prompt into the agent on its platform. Results show on the release, with retry.

Every session records the release it ran on. Pin a release per call for A/B tests (e.g. 70% live, 30% a candidate).

**LiveKit (pull, with the SDK).** `pip install "./sdks/python[livekit]"` ([sdks/python](sdks/python)), set `SOPOPS_URL` (`http://sopops:8484/v1/workspaces/<ws>`) and `SOPOPS_TOKEN`:

```python
from sopops_client.livekit import attach

async def entrypoint(ctx: JobContext):
    await ctx.connect()
    session = AgentSession(...)
    ins = await attach(ctx, session, agent="tonys-pizza")
    await session.start(room=ctx.room, agent=Agent(instructions=ins.text))
```

`attach` fetches the instructions (a release pinned as `{"sopops_release": 3}` in job or room metadata wins), keeps the release in `session.userdata`, and posts the session at shutdown. If sopops is down it uses the last good copy on disk, so it never blocks a call.

**ElevenLabs.**
- *Push:* add the API key in Settings → Platforms (Test reads one agent) and set the agent to push. sopops writes `conversation_config.agent.prompt.prompt`.
- *Pull for phone calls:* set the workspace's conversation-initiation webhook to `…/runtime/elevenlabs/conversation-init` with the webhook secret as an `X-Sopops-Secret` header, and allow the agent to override its system prompt (Security → Overrides).
- *Pull from a web app:* the TypeScript client ([sdks/node](sdks/node)) builds `Conversation.startSession` overrides.

**Vapi.**
- *Push:* add the API key and set the agent to push. sopops reads the assistant, replaces the system message in `model.messages`, and writes the model back.
- *Pull:* point the phone number's Server URL at `…/runtime/vapi/assistant-request?agent=<id>`, with a Bearer Token credential set to the webhook secret. sopops answers with the assistant id and the prompt in `assistantOverrides.model`.

**Anything else:** `Sopops().fetch_instructions(agent)` (Python) or `new Sopops().fetchInstructions(agent)` (TypeScript), or plain HTTP: `GET …/agents/<agent>` with `Authorization: Bearer <token>`.

**Tokens.** Make tokens in Settings → Tokens, each with the scopes it needs: `runtime` for prompt fetches and call-start webhooks, `ingest` for sending sessions (one token can have both; the SDKs read it from `SOPOPS_TOKEN`). Tokens are shown once, stored hashed, and can expire or be revoked. Once a workspace has a runtime token, prompt fetches need one; a workspace without one serves prompts openly, for local use. When sopops is hosted, set `SOPOPS_REQUIRE_WEBHOOK_SECRET=true` so call-start webhooks are refused for a workspace with no webhook secret or runtime token instead of answered openly. Platform API keys and webhook secrets are encrypted with `SOPOPS_SECRET_KEY` and never returned by the API.

## Sessions

A session is one conversation an agent had: a voice call or a text chat. Agents send each finished session to sopops, with the release it ran on, and the Sessions page lists them live, with filters, each one's transcript, tool calls and metadata, and how it went against each procedure: every step, never rule and warning sign, with the quotes that show it highlighted in the transcript.

Every source converts into one canonical JSON format ([API.md](API.md#sessions)), sent to `POST …/sessions` with a token that has the `ingest` scope (Settings → Tokens, shown once). Sending the same session again is safe: it's keyed by platform and session id.

**LiveKit.** `attach` from the SDK (above) posts `session.history` at shutdown, with timestamps and tool calls, keyed by the job id, with the release. Without the SDK, copy [`helpers/livekit`](helpers/livekit) next to your agent.

**ElevenLabs and Vapi** post-call webhooks are next. Until then, send their transcripts in the canonical format. A call whose prompt came from a sopops webhook (or that the TypeScript client reported) links to that release by its conversation or call id.

**Files.** `sopops import sessions.jsonl --workspace prod` reads one canonical session per line straight into the database, e.g. a backfill or [`examples/restaurant-sessions.jsonl`](examples/restaurant-sessions.jsonl).

**The pipeline.** Each new or changed session runs `process_session`:

1. **normalize** the turns and tool calls;
2. **route** (the fast model): which of the agent's procedures, in the release the session ran on, applied, and which to leave out because the session ended before the procedure started or finished;
3. **check** (the reasoning model, one call per procedure that applied): a status for every rule, with quotes, and whether the procedure's goal was reached. Required tool steps are checked against the tool calls, when the session has them;
4. a **result** per procedure, and the worst of them for the session: deviated, partial, followed or not measurable ([how](API.md#sop-checks));
5. **patterns**: each finding joins its rule's pattern, and once a rule has enough findings, a model splits it into named failure modes, so the same mistake shows up once with its count. Themes group them by the phase of the session ([how](API.md#patterns-and-themes)).

Without Hatchet it runs inside sopops, after the response. With `HATCHET_CLIENT_TOKEN` set it runs on Hatchet in `sopops worker`: one run per session version, a fair share per workspace, one check per procedure in parallel, and model calls rate-limited per model (`SOPOPS_RATE_LIMIT_PER_MINUTE`, default 60). `docker compose --profile hatchet up` runs hatchet-lite (see [docker-compose.yml](docker-compose.yml)). Live updates reach the Sessions page from any process on Postgres (LISTEN/NOTIFY); on SQLite, only from the sopops process itself.

Models are called through [LiteLLM](https://github.com/BerriAI/litellm), as a library (pinned, with hashes in `uv.lock`), so any provider it supports works, including a local model behind an OpenAI-compatible endpoint. Prompts are in [`src/sopops/pipeline/prompts`](src/sopops/pipeline/prompts), and every result records the model and prompt version that produced it.

## Insights and the report

**Insights** shows how sessions went against their procedures over a window (24 hours to a year): sessions checked, followed end to end, needing attention and excluded, against the prior window; a trend with release markers; a scoreboard per procedure. A procedure's page has its rule ladder (pass and skip rates per step and never rule, with n), the split between the last two releases, how warning signs relate to the pass rate, and its patterns by theme. Each rule has its own page, and **Release compare** puts two releases side by side, rule by rule, with 95% intervals and no flag under 10 sessions a side. Every number links to the sessions behind it. Definitions: [API.md](API.md#insights).

The **Report** reads like a hand-written call review: the headline numbers, then each theme and its patterns, every one with the rule it's about ("Your procedure says never: …"), how the agent phrased it, and the sessions.

## Git or database

**Database.** Everything lives in sopops's database and is edited in the UI or over the `/config` API. Nobody edits YAML or Markdown, but each block is still stored as a sopc file and versioned on its own.

**Git.** The sopc folder lives in your repo, and CI publishes it on merge in one call:

```sh
python3 -c 'import json,pathlib; r=pathlib.Path("sops"); print(json.dumps({"files": {p.relative_to(r).as_posix(): p.read_text() for p in r.rglob("*") if p.is_file()}}))' \
  | curl -fsS -X POST -H 'Content-Type: application/json' -d @- "$SOPOPS_URL/publish"
```

`build/` is ignored. A folder in the format of sopc v0.0.8 or earlier, or one that still sets `locked` (sopc v0.0.9), is refused with a message to run `sopc migrate`; a workspace stored that way shows a Convert page in the UI.

You can switch any time: `GET …/files?content=true` exports a workspace as sopc files to commit.

## API

Paths are under `/v1/workspaces/<ws>`. The full spec is at `/openapi.json` (SDKs generate from it), and [API.md](API.md) lists every endpoint.

| Endpoint | What it does |
|---|---|
| `GET …/agents/<agent>` | **Call start.** The prompt, release, hash, tools and the components it was built from. `?release=N` pins a release. |
| `GET …/agents/<agent>/prompt` | Just the prompt text. |
| `…/runtime/delivery`, `…/runtime/platforms` | Each agent's delivery mode; platform API keys and webhook secrets (write-only). |
| `GET …/releases/<n>/pushes`, `POST …/pushes/retry` | What publishing pushed to ElevenLabs and Vapi; push again. |
| `POST …/runtime/elevenlabs/conversation-init`, `…/runtime/vapi/assistant-request` | Call-start webhooks that serve the prompt. |
| `POST …/sessions` | **Session end.** Ingest a session in the canonical format, with an `ingest` token. |
| `GET/POST …/tokens`, `DELETE …/tokens/<id>` | Workspace tokens with scopes (`ingest`, `runtime`), shown once; revoke. |
| `GET …/sessions`, `…/sessions/<id>`, `…/sessions/stream` | List sessions (filters, cursor), one with its transcript and SOP checks, and live events (SSE). |
| `GET …/config` | Every agent, shared instruction, procedure, group and setting, as the forms edit them. |
| `…/config/{agents,instructions,procedures,groups}[/<id>]` | List, create, read, replace and delete each kind, with history. |
| `POST …/config/agents/<id>/blocks`, `DELETE …/blocks/<block>` | Add a block at the end of an agent's list, or take one out. |
| `POST …/config/agents/<id>/preview` | Compile an agent's unsaved fields, without saving. |
| `GET …/draft` | Unpublished changes: each item before and after, and each agent's prompt diff. |
| `POST …/releases`, `POST …/releases/<n>/activate` | Publish head; make an older release live. |
| `POST …/publish` | Replace every file and release, for CI. |
| `POST …/migrate` | Convert a workspace stored in an older sopc format (v0.0.8, or `locked` from v0.0.9). |
| `…/imports`, `/v1/onboarding/*` | Import existing prompts: extraction jobs, onboarding tokens for coding agents, review and apply. |

Errors from sopc come back as `422 {"valid": false, "issues": [...]}`, each issue naming the item and field it's about, in plain words.

## Status

v0, renamed from sopserve. **There is no authentication yet** except workspace tokens, which guard session ingest, onboarding pushes and, once the workspace has a runtime token, prompt fetches. Anyone who can reach sopops can read and change everything else, so run it on a private network only.

## Roadmap

- Verdicts on patterns (real, acceptable, SOP wrong, checker wrong) and a Slack digest
- ElevenLabs and Vapi post-call webhooks, and an OpenTelemetry receiver
- GitHub: load a sopc project from a repo path, and open PRs with suggested changes
- Sign-in for the UI, and tokens for the rest of the API
- Renaming agents and blocks
- A webhook on publish
- A LiveKit adapter in the TypeScript client; more framework adapters (Pipecat)

## Development

The backend is FastAPI with SQLModel and Alembic (`src/sopops`); the UI is Vite, React, TanStack Router and Query, Tailwind and shadcn/ui (`web/`). In production sopops serves the built UI itself, one process.

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
| `uv run alembic revision --autogenerate -m "…"` | A new migration after changing `models.py`. |

## License

Apache-2.0
