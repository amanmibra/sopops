# Configuration

## Core

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | `sqlite:///sopops.db` | Postgres (`postgresql://`, needs `pip install 'sopops-server[postgres]'`) or SQLite. Alembic migrates on startup. |
| `SOPC_BIN` | `sopc` on `PATH` | The sopc binary, v0.0.17 or later. |
| `HOST`, `PORT` | `127.0.0.1`, `8484` | Also `--host`, `--port`. |
| `SOPOPS_SECRET_KEY` | unset | Fernet key that encrypts platform keys, webhook secrets and GitHub tokens. Required to store them. |
| `SOPOPS_CORS_ORIGINS` | unset | Comma-separated browser origins allowed to call sopops. |
| `SOPOPS_WEB_DIR` | the built UI | Serve the UI from another folder. |

Make a `SOPOPS_SECRET_KEY`:

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Models

See [Models](sessions-and-review.md#models). These are the defaults: each team can replace any of them with its own keys and models in Settings, under Checks.

| Variable | Default | |
|---|---|---|
| `SOPOPS_MODEL_FAST`, `SOPOPS_MODEL_REASONING` | unset | The two model roles. `SOPOPS_MODEL_REASONING=rules` tries importing without a model. |
| `SOPOPS_LLM_API_BASE`, `SOPOPS_LLM_TIMEOUT`, `SOPOPS_MAX_TOKENS` | unset, `300`, `16000` | OpenAI-compatible base URL for every call; per-call timeout (s); output limit. |
| `TYPESAFE_API_KEY`, `SOPOPS_JEV`, `SOPOPS_JEV_MODEL` | unset, auto, `jev-1.13.0` | Jev for decisions. On when the key is set, unless `SOPOPS_JEV=off`. |
| `SOPOPS_CHECK_MODE`, `SOPOPS_CHECK_REVIEW`, `SOPOPS_DECISIONS` | `decide`, `on`, unset | Check mode; reviewer on or off; decision thresholds and bands as JSON, e.g. `{"never": [0.05, 0.7, 0.7]}`. |
| `SOPOPS_MODEL_PRICES` | unset | Prices for spend estimates, JSON in USD per million tokens, e.g. `{"openai/gpt-5-mini": [0.25, 2]}`. Adds to or overrides the built-in table (Jev, current Claude models). |
| `SOPOPS_DSPY_CACHE_DIR` | unset | Disk cache for model responses (transcripts are written there). In memory otherwise. |

## Sign-in and email

See [Sign in](sign-in.md).

| Variable | Default | |
|---|---|---|
| `SOPOPS_GITHUB_OAUTH_CLIENT_ID`, `SOPOPS_GITHUB_OAUTH_CLIENT_SECRET` | unset | Sign in with GitHub. |
| `SOPOPS_GOOGLE_CLIENT_ID`, `SOPOPS_GOOGLE_CLIENT_SECRET` | unset | Sign in with Google. |
| `SOPOPS_SMTP_HOST`, `SOPOPS_SMTP_PORT`, `SOPOPS_SMTP_USER`, `SOPOPS_SMTP_PASSWORD`, `SOPOPS_SMTP_FROM` | unset, `587` | Email for sign-in links and invites. Port 465 is TLS from the start; others use STARTTLS when offered. `SOPOPS_SMTP_FROM` is the sender, defaulting to the user if that's an address. |
| `SOPOPS_EMAIL_DEV` | unset | `1`: email sign-in without SMTP. Links go to the server log. Local use and demos only. |
| `SOPOPS_AUTH_REQUIRED` | on when GitHub, Google or email is set up | `true` or `false` forces sign-in. `true` with nothing set up: email and password sign-in. |
| `SOPOPS_OPEN_SIGNUP` | `false` | `true` with sign-in on: anyone can sign up (name, email, password, team name) and owns the new team. Teams use this server's model keys, so leave it off unless you host sopops for anyone. |
| `SOPOPS_ALLOWED_EMAIL_DOMAINS` | unset | Comma-separated. Only verified emails at these domains can sign in. With email set up, they can also make a password account. |
| `SOPOPS_PUBLIC_URL` | the request's own address | Where people reach sopops. Base of sign-in callbacks and invite links. |

## Platforms and webhooks

| Variable | Default | |
|---|---|---|
| `SOPOPS_REQUIRE_WEBHOOK_SECRET` | `false` | Set `true` when hosted. Refuses call-start webhooks for a workspace with no webhook secret or runtime token, instead of answering openly. |
| `SOPOPS_SESSION_IDLE_MINUTES` | `30` | A streamed session with no events this long closes as incomplete. |
| `SOPOPS_POSTCALL_GRACE_SECONDS` | `120` | After ElevenLabs' monitor socket ends, how long to wait for the post-call webhook (if set up) before closing and checking. |
| `SOPOPS_LIVEKIT_ANY_HOST` | `false` | Allow a self-hosted LiveKit URL, not only LiveKit Cloud. |
| `SOPOPS_MCP_SOURCE` | `sopops[mcp] @ git+https://github.com/amanmibra/sopops@main#subdirectory=sdks/python` | What the onboarding command's `uvx --from` installs: the client SDK with its `mcp` extra. From a clone: `/path/to/sopops/sdks/python[mcp]`. |

## GitHub

| Variable | Default | |
|---|---|---|
| `SOPOPS_GITHUB_API_URL` | `https://api.github.com` | GitHub Enterprise Server: `https://HOST/api/v3`. Also set `SOPOPS_GITHUB_URL` to `https://HOST` for its install pages. |
| `SOPOPS_GITHUB_APP_ID`, `SOPOPS_GITHUB_APP_PRIVATE_KEY` (or `…_PATH`), `SOPOPS_GITHUB_WEBHOOK_SECRET`, `SOPOPS_GITHUB_APP_SLUG` | unset | The GitHub App sopops signs in as: id, private key (PEM or file), webhook secret, and name in `github.com/apps/<slug>`. Without them, Settings offers to create one (stored encrypted with `SOPOPS_SECRET_KEY`). |

## Hatchet

| Variable | Default | |
|---|---|---|
| `HATCHET_CLIENT_TOKEN` | unset | Run the pipeline on Hatchet. Needs `pip install 'sopops-server[hatchet]'` and `sopops worker`. |
| `SOPOPS_RATE_LIMIT_PER_MINUTE` | `60` | Model calls per minute per model. |
| `SOPOPS_WORKSPACE_CONCURRENCY` | `4` | Runs per workspace at once. |
| `SOPOPS_WORKER_SLOTS` | `20` | Worker slots. |
| `SOPOPS_RECLUSTER_CRON` | `0 6 * * *` | Daily recluster. |
| `SOPOPS_PR_STATUS_CRON` | `*/15 * * * *` | Check pull requests and sync connected repositories. |

These apply with Hatchet only. Without it, run `sopops recluster` and `sopops pr-status` from cron.
