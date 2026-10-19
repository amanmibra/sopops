# Contributing

## Set up

You need [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io) and the [sopc](https://github.com/amanmibra/sopc) binary, v0.0.17 or later.

```sh
uv sync --all-extras
SOPC_BIN=/path/to/sopc uv run sopops    # API on :8484, and the built UI
pnpm -C web install && pnpm -C web dev   # UI on :5173, proxying /v1 to :8484
```

## Where things are

| Path | |
|---|---|
| `src/sopops_server/api/` | HTTP routers, one per area; `app.py` mounts them |
| `src/sopops_server/schemas/` | Request and response shapes, one module per area |
| `src/sopops_server/orm/` | Database tables, one module per table; migrations in `migrations/` |
| `src/sopops_server/ai/` | Model calls: `signatures` (every DSPy signature), `lm` (models per role), `decide`, `programs`, `calibration`, `fakes` (for tests) |
| `src/sopops_server/pipeline/` | The session pipeline: normalize, route, check, rollup |
| `src/sopops_server/onboarding/`, `runtime/`, `jobs/` | Importing agents; delivering instructions to platforms; inline or Hatchet runs |
| `src/sopops_server/auth/` | Sign-in, teams and access; `access.py` holds every route's rule in one table |
| `src/sopops_server/store.py` and the modules beside it | The data layer and services, by area |
| `src/sopops_server/cli.py` | The `sopops` command |
| `web/` | The UI; `web/src/client` is generated from the OpenAPI spec |
| `sdks/python`, `sdks/node` | Client SDKs; `sdks/python` also holds `sopops-mcp` (the `mcp` extra) |
| `skills/sopops-onboard` | The onboarding skill coding agents follow |
| `examples/` | The walkthrough, a LiveKit agent, an ElevenLabs web app, session import |

## Commands

| Command | |
|---|---|
| `uv run pytest` | Backend tests, on SQLite. `SOPOPS_TEST_DATABASE_URL=postgresql://…` runs them on Postgres. |
| `cd sdks/python && uv run pytest`, `pnpm -C sdks/node test` | SDK tests |
| `pnpm -C web build` | Typecheck and build the UI into `web/dist` |
| `pnpm -C web gen:api` | Regenerate `web/src/client` from the OpenAPI spec |
| `uv run alembic revision --autogenerate -m "…"` | A migration after changing a table |
| `uv run sopops worker` | The Hatchet worker, with `HATCHET_CLIENT_TOKEN` set |
| `uv run sopops-mcp` | The onboarding MCP server over stdio, as Claude Code or Codex runs it |
| `just examples/walkthrough/all` | The whole loop end to end with fake models |

## Conventions

- **One table per file** in `orm/`, and a migration for every table change.
- **Imports at the top of the module.** The exception is an optional dependency (`livekit`, `hatchet`, `jev`), imported where it's used.
- **"Model" means an AI model.** Call database classes tables or rows.
- **API changes ship with the client.** Run `pnpm -C web gen:api` and commit `web/src/client` in the same change.
- **UI copy uses plain words.** Say "instructions", not "prompt", and name things the way a user would. No em or en dashes.
- **No customer names** in code, tests, docs or examples. Use made-up names like Northwind or Harbor Lane.
- **Tests run without keys.** Use the fakes in `ai/fakes.py`, never a real model.

## Pull requests

Keep each one to one change, with tests. Run `uv run pytest` and `pnpm -C web build` before you open it.
