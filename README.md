<div align="center">

# 📋🗄️ sopops

**Serves sopc agent prompts from a UI and an API, collects your agents' sessions, and checks each one against its procedures. Patterns across sessions and GitHub PRs are coming.**

Edit shared instructions and procedures in a browser, compose each agent from them,<br>
and publish releases your agents fetch when a call starts. No redeploys.

[Concepts](https://github.com/amanmibra/whatsop) · [Why](#why) · [Quickstart](#quickstart) · [How it works](#how-it-works) · [Use it from your agent](#use-it-from-your-agent) · [Sessions](#sessions) · [Git or database](#git-or-database) · [API](#api)

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
| `SOPOPS_MODEL_FAST`, `SOPOPS_MODEL_REASONING` | unset | The models that check sessions, as [LiteLLM](https://docs.litellm.ai/docs/providers) model strings (e.g. `anthropic/claude-haiku-4-5` and `anthropic/claude-sonnet-5-5`), with the provider's key in its usual variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...). Unset, sessions are stored but not checked. |
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

## Use it from your agent

At room start, fetch the prompt and record what the call ran on:

```python
import json, os, httpx
from livekit import api
from livekit.agents import Agent, AgentSession, JobContext

SOPOPS = os.environ["SOPOPS_URL"]  # e.g. http://sopops.internal:8484/v1/workspaces/prod


async def fetch_prompt(agent_name: str, release: str | None = None) -> dict:
    """The agent's compiled prompt, plus the release and component versions it was built from."""
    params = {"release": release} if release else {}
    async with httpx.AsyncClient(timeout=5) as http:
        res = await http.get(f"{SOPOPS}/agents/livekit:{agent_name}", params=params)
        res.raise_for_status()
        return res.json()


async def record_versions(room: str, sop: dict) -> None:
    """Save what this call ran on in the room metadata, so QA can trace it later."""
    async with api.LiveKitAPI() as lk:  # LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
        await lk.room.update_room_metadata(api.UpdateRoomMetadataRequest(
            room=room,
            metadata=json.dumps({"sopc": {k: sop[k] for k in ("release", "hash", "components")}}),
        ))


async def entrypoint(ctx: JobContext):
    await ctx.connect()
    sop = await fetch_prompt(ctx.job.agent_name, release=os.environ.get("SOPC_RELEASE"))
    await record_versions(ctx.room.name, sop)

    session = AgentSession(...)
    await session.start(room=ctx.room, agent=Agent(instructions=sop["prompt"]))
```

For A/B tests, have your router pick a release per call (e.g. 70% current, 30% a candidate) and pass it as `?release=N`; the room metadata and the fetch log record which one ran.

## Sessions

A session is one conversation an agent had: a voice call or a text chat. Agents send each finished session to sopops, with the release it ran on, and the Sessions page lists them live, with filters, each one's transcript, tool calls and metadata, and how it went against each procedure: every step, never rule and warning sign, with the quotes that show it highlighted in the transcript.

Every source converts into one canonical JSON format ([API.md](API.md#sessions)), sent to `POST …/sessions` with the workspace's ingest token (Settings → Ingest token, shown once). Sending the same session again is safe: it's keyed by platform and session id.

**LiveKit.** Copy [`helpers/livekit/python/sopops_livekit.py`](helpers/livekit/python/sopops_livekit.py) (or the [Node version](helpers/livekit/node/sopops-livekit.ts)) next to your agent. At shutdown it posts `session.history` with timestamps and tool calls, keyed by the job id, with the release from `session.userdata`:

```python
from sopops_livekit import record_session

async def entrypoint(ctx: JobContext):
    await ctx.connect()
    sop = await fetch_prompt(ctx.job.agent_name)                    # as above
    session = AgentSession(..., userdata={"sopops_release": sop["release"]})
    record_session(ctx, session)                                    # SOPOPS_URL, SOPOPS_INGEST_TOKEN
    await session.start(room=ctx.room, agent=Agent(instructions=sop["prompt"]))
```

**ElevenLabs and Vapi** post-call webhooks are next. Until then, send their transcripts in the canonical format.

**Files.** `sopops import sessions.jsonl --workspace prod` reads one canonical session per line straight into the database, e.g. a backfill or [`examples/restaurant-sessions.jsonl`](examples/restaurant-sessions.jsonl).

**The pipeline.** Each new or changed session runs `process_session`:

1. **normalize** the turns and tool calls;
2. **route** (the fast model): which of the agent's procedures, in the release the session ran on, applied, and which to leave out because the session ended before the procedure started or finished;
3. **check** (the reasoning model, one call per procedure that applied): a status for every rule, with quotes, and whether the procedure's goal was reached. Required tool steps are checked against the tool calls, when the session has them;
4. a **result** per procedure, and the worst of them for the session: deviated, partial, followed or not measurable ([how](API.md#sop-checks)).

Without Hatchet it runs inside sopops, after the response. With `HATCHET_CLIENT_TOKEN` set it runs on Hatchet in `sopops worker`: one run per session version, a fair share per workspace, one check per procedure in parallel, and model calls rate-limited per model (`SOPOPS_RATE_LIMIT_PER_MINUTE`, default 60). `docker compose --profile hatchet up` runs hatchet-lite (see [docker-compose.yml](docker-compose.yml)). Live updates reach the Sessions page from any process on Postgres (LISTEN/NOTIFY); on SQLite, only from the sopops process itself.

Models are called through [LiteLLM](https://github.com/BerriAI/litellm), as a library (pinned, with hashes in `uv.lock`), so any provider it supports works, including a local model behind an OpenAI-compatible endpoint. Prompts are in [`src/sopops/pipeline/prompts`](src/sopops/pipeline/prompts), and every result records the model and prompt version that produced it.

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
| `POST …/sessions` | **Session end.** Ingest a session in the canonical format, with the ingest token. |
| `GET …/sessions`, `…/sessions/<id>`, `…/sessions/stream` | List sessions (filters, cursor), one with its transcript and SOP checks, and live events (SSE). |
| `GET …/config` | Every agent, shared instruction, procedure, group and setting, as the forms edit them. |
| `…/config/{agents,instructions,procedures,groups}[/<id>]` | List, create, read, replace and delete each kind, with history. |
| `POST …/config/agents/<id>/blocks`, `DELETE …/blocks/<block>` | Add a block at the end of an agent's list, or take one out. |
| `POST …/config/agents/<id>/preview` | Compile an agent's unsaved fields, without saving. |
| `GET …/draft` | Unpublished changes: each item before and after, and each agent's prompt diff. |
| `POST …/releases`, `POST …/releases/<n>/activate` | Publish head; make an older release live. |
| `POST …/publish` | Replace every file and release, for CI. |
| `POST …/migrate` | Convert a workspace stored in an older sopc format (v0.0.8, or `locked` from v0.0.9). |

Errors from sopc come back as `422 {"valid": false, "issues": [...]}`, each issue naming the item and field it's about, in plain words.

## Status

v0, renamed from sopserve. **There is no authentication yet** except the ingest token for sending sessions: anyone who can reach sopops can read and change everything else, so run it on a private network only.

## Roadmap

- Patterns across sessions' findings, insights per procedure and release, and verdicts
- ElevenLabs and Vapi post-call webhooks, and an OpenTelemetry receiver
- GitHub: load a sopc project from a repo path, and open PRs with suggested changes
- API keys with scopes, and sign-in for the UI
- Renaming agents and blocks
- A webhook on publish

## Development

The backend is FastAPI with SQLModel and Alembic (`src/sopops`); the UI is Vite, React, TanStack Router and Query, Tailwind and shadcn/ui (`web/`). In production sopops serves the built UI itself, one process.

```sh
uv sync --all-extras
SOPC_BIN=/path/to/sopc uv run sopops        # the API on :8484 (and the UI, once built)
pnpm -C web install && pnpm -C web dev       # the UI on :5173, proxying /v1 to :8484
```

| | |
|---|---|
| `uv run pytest` | Backend tests, on SQLite. `SOPOPS_TEST_DATABASE_URL=postgresql://…` runs them on Postgres. Hatchet isn't needed. |
| `uv run sopops import FILE.jsonl -w WS` | Import sessions. |
| `uv run sopops worker` | The Hatchet worker, with `HATCHET_CLIENT_TOKEN` set. |
| `pnpm -C web build` | Typecheck and build the UI into `web/dist`, which `sopops` serves. |
| `pnpm -C web gen:api` | Regenerate the typed client (`web/src/client`) from the API's OpenAPI spec. Commit it with the API change. |
| `uv run alembic revision --autogenerate -m "…"` | A new migration after changing `models.py`. |

## License

Apache-2.0
