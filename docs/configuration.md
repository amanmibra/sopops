# Configuration

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | `sqlite:///sopops.db` | Postgres (`postgresql://`, with `pip install 'sopops[postgres]'`) or SQLite. Migrated on startup (Alembic). |
| `SOPC_BIN` | `sopc` on `PATH` | The sopc binary, v0.0.17 or later. |
| `HOST`, `PORT` | `127.0.0.1`, `8484` | Also `--host`, `--port`. |
| `SOPOPS_MODEL_FAST`, `SOPOPS_MODEL_REASONING` | unset | The two model roles ([Models](sessions-and-review.md#models)); `SOPOPS_MODEL_REASONING=rules` tries importing without a model. |
| `SOPOPS_LLM_API_BASE`, `SOPOPS_LLM_TIMEOUT`, `SOPOPS_MAX_TOKENS` | unset, `300`, `16000` | An OpenAI-compatible base URL for every model call; per-call timeout (s) and output limit. |
| `TYPESAFE_API_KEY`, `SOPOPS_JEV`, `SOPOPS_JEV_MODEL` | unset, auto, `jev-1.13.0` | Jev for decisions: on when the key is set, unless `SOPOPS_JEV=off`. |
| `SOPOPS_CHECK_MODE`, `SOPOPS_CHECK_REVIEW`, `SOPOPS_DECISIONS` | `decide`, `on`, unset | How rules are checked; reviewer on or off; decision thresholds and bands as JSON, e.g. `{"never": [0.05, 0.7, 0.7]}`. |
| `SOPOPS_MODEL_PRICES` | unset | Model prices for spend estimates, JSON in USD per million tokens: `{"openai/gpt-5-mini": [0.25, 2]}`; adds to or overrides the built-in table (Jev, current Claude models). |
| `SOPOPS_DSPY_CACHE_DIR` | unset | A disk cache for model responses (transcripts are written there); in memory otherwise. |
| `SOPOPS_SECRET_KEY` | unset | A Fernet key that encrypts platform keys, webhook secrets and GitHub tokens; needed to store them. Make one: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |
| `SOPOPS_GITHUB_OAUTH_CLIENT_ID`, `SOPOPS_GITHUB_OAUTH_CLIENT_SECRET` | unset | Sign in with GitHub ([Sign in](sign-in.md)). |
| `SOPOPS_GOOGLE_CLIENT_ID`, `SOPOPS_GOOGLE_CLIENT_SECRET` | unset | Sign in with Google. |
| `SOPOPS_SMTP_HOST`, `SOPOPS_SMTP_PORT`, `SOPOPS_SMTP_USER`, `SOPOPS_SMTP_PASSWORD`, `SOPOPS_SMTP_FROM` | unset, `587` | Email: sign-in links and invites. Port 465 is TLS from the start; other ports use STARTTLS when offered. `SOPOPS_SMTP_FROM` is the sender (defaults to the user, if that's an address). |
| `SOPOPS_EMAIL_DEV` | unset | `1`: email sign-in on without SMTP. Links go to the server log instead of being sent. For local use and demos only. |
| `SOPOPS_AUTH_REQUIRED` | on when GitHub, Google or email is set up | `true` or `false` forces sign-in on or off. `true` with nothing else set up: people sign in with an email and password. |
| `SOPOPS_OPEN_SIGNUP` | `false` | `true`, with sign-in on: anyone can sign up with a name, email, password and team name, and owns that new team ([Sign in](sign-in.md)). Teams use this server's model keys, so leave it off unless you mean to host sopops for anyone. |
| `SOPOPS_ALLOWED_EMAIL_DOMAINS` | unset | Comma-separated: only people with a verified email at these domains can sign in at all. With email set up, people at these domains can also make an account with a password. |
| `SOPOPS_PUBLIC_URL` | the request's own address | Where people reach sopops: the base of sign-in callback URLs and invite links. |
| `SOPOPS_REQUIRE_WEBHOOK_SECRET` | `false` | Set `true` when sopops is hosted: call-start webhooks are refused for a workspace with no webhook secret or runtime token, instead of answered openly. |
| `SOPOPS_CORS_ORIGINS` | unset | Comma-separated origins allowed to call sopops from a browser. |
| `SOPOPS_SESSION_IDLE_MINUTES` | `30` | An open (streamed) session with no events this long closes as incomplete. |
| `SOPOPS_POSTCALL_GRACE_SECONDS` | `120` | After ElevenLabs' monitor socket ends, how long its session waits for the post-call webhook (when one is set up) before it closes and is checked. |
| `SOPOPS_LIVEKIT_ANY_HOST` | `false` | Allow a self-hosted LiveKit server's URL, not only LiveKit Cloud's. |
| `SOPOPS_MCP_SOURCE` | `sopops-client[mcp] @ git+https://github.com/amanmibra/sopops@main#subdirectory=sdks/python` | What the onboarding command's `uvx --from` installs: the client SDK with its `mcp` extra. A clone: `/path/to/sopops/sdks/python[mcp]`. |
| `SOPOPS_GITHUB_API_URL` | `https://api.github.com` | GitHub Enterprise Server: `https://HOST/api/v3` (and `SOPOPS_GITHUB_URL`, `https://HOST`, for its install pages). |
| `SOPOPS_GITHUB_APP_ID`, `SOPOPS_GITHUB_APP_PRIVATE_KEY` (or `…_PATH`), `SOPOPS_GITHUB_WEBHOOK_SECRET`, `SOPOPS_GITHUB_APP_SLUG` | unset | The GitHub App sopops signs in as: its id, its private key (PEM, or a file), the webhook secret, and its name in `github.com/apps/<slug>`. Without them, Settings offers to create one (stored encrypted with `SOPOPS_SECRET_KEY`). |
| `HATCHET_CLIENT_TOKEN` | unset | Run the pipeline on Hatchet (`pip install 'sopops[hatchet]'`, plus `sopops worker`). |
| `SOPOPS_RATE_LIMIT_PER_MINUTE`, `SOPOPS_WORKSPACE_CONCURRENCY`, `SOPOPS_WORKER_SLOTS`, `SOPOPS_RECLUSTER_CRON`, `SOPOPS_PR_STATUS_CRON` | `60`, `4`, `20`, `0 6 * * *`, `*/15 * * * *` | Hatchet only: model calls per minute per model, runs per workspace at once, worker slots, the daily recluster, checking pull requests and syncing connected repositories (without Hatchet, run `sopops recluster` and `sopops pr-status` from cron). |
| `SOPOPS_WEB_DIR` | the built UI | Serve the UI from another folder. |
