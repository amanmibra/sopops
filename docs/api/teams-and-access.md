# Teams and access

Signing in, teams and their members, workspace tokens, and each team's AI keys and models.

## Sign-in and access

Sign-in is on once a login provider is set up, and `SOPOPS_AUTH_REQUIRED=true|false` forces it either way. A provider is any of:
- GitHub: `SOPOPS_GITHUB_OAUTH_CLIENT_ID` and `_SECRET`;
- Google: `SOPOPS_GOOGLE_CLIENT_ID` and `_SECRET`;
- email: `SOPOPS_SMTP_HOST` and `SOPOPS_SMTP_FROM`, or `SOPOPS_EMAIL_DEV=1`.

See [Sign-in](../sign-in.md). With sign-in on, email and password sign-in is always offered; `true` with nothing else set up means passwords only.

With sign-in on, each endpoint is one of these kinds. The table is `ROUTES` in `src/sopops_server/auth/access.py`, and a test checks every route is in it.

| Kind | Needs | Endpoints |
|---|---|---|
| open | nothing | `/v1/auth/session`, `/v1/auth/login/{provider}`, `/v1/auth/callback/{provider}`, `POST /v1/auth/email`, `POST /v1/auth/email/confirm`, `POST /v1/auth/invites/lookup`, `POST /v1/auth/invites/join`, `POST /v1/auth/password/{login,first-account,email,link,set}`, `POST /v1/auth/sign-up`, `POST /v1/auth/join/lookup`, `POST /v1/auth/join`, `/healthz`, `/docs`, `/openapi.json`, the web UI's files |
| token | the endpoint's own token, no login | `POST /v1/workspaces/{ws}/sessions` (and `/v1/workspaces/{ws}/open`, `/v1/workspaces/{ws}/events`, `/v1/workspaces/{ws}/close`): `ingest`. `POST /v1/workspaces/{ws}/runtime/served`: `runtime`. `/v1/onboarding/{session,kickoff,guide,extraction,project,progress,proposal,pull-request,pull-request/open,fidelity}`: `onboarding:write` |
| webhook | the sender's signature or secret, no login | `POST /v1/github/webhook`, `/v1/workspaces/{ws}/capture/livekit/webhook`, `/v1/workspaces/{ws}/capture/elevenlabs/post-call`, `/v1/workspaces/{ws}/runtime/elevenlabs/conversation-init`, `/v1/workspaces/{ws}/runtime/vapi/assistant-request` |
| token or session | a `runtime` token, or a login session | `GET /v1/workspaces/{ws}/agents/{agent}`, `/v1/workspaces/{ws}/prompt`, `/v1/workspaces/{ws}/sops/{id}`. With sign-in on these always need one, even before the workspace has a runtime token |
| signed in | a login session, with or without an org | `POST /v1/auth/logout`, `PUT /v1/auth/org`, `POST /v1/auth/invites/accept`, `POST /v1/auth/join/accept`, `POST /v1/auth/password/change` |
| admin | a login session as an owner or admin | `PATCH /v1/org`, `POST /v1/org/members`, `PATCH/DELETE /v1/org/members/{user_id}`, `POST /v1/org/members/{user_id}/password-link`, `GET/POST /v1/org/invites`, `POST /v1/org/invites/{id}/resend`, `DELETE /v1/org/invites/{id}`, `POST /v1/org/setup`, `GET/POST/DELETE /v1/org/join-link`, `POST /v1/github/app/manifest` and its callback, `/v1/workspaces/{ws}/capture/{platform}/backfill` and its preview |
| session | a login session as a member of the workspace's org | everything else, including `POST /v1/orgs` and `PATCH /v1/org/platforms` |

Without a session: `401 {"detail": "sign in first"}`. Signed in but in no org: `403`. A workspace in an org you're not in answers `404`. A new workspace goes into the org you're working in.

- **Login sessions.** The `sopops_session` cookie: HttpOnly, SameSite=Lax, Secure on https, a random token stored hashed. It lasts 30 days from its last use (`GET /v1/auth/session` renews the cookie) and ends at sign-out.
- **CSRF.** With the session cookie, `POST`, `PUT`, `PATCH` and `DELETE` must send `X-CSRF-Token` with the `sopops_csrf` cookie's value, or get `403`. That value is an HMAC of the session token, readable by the page and not by other sites. Token and webhook endpoints take no cookie and need none. The open endpoints that sign in take JSON only, which another site can't make a browser send without passing CORS.
- **Authors.** Signed in, the user is recorded wherever an author is (file versions, releases, verdicts, overrides, suggestions, imports, tokens): their name in the text field (`author`, `reviewer`, `created_by`), whatever the request sent, and their id in `author_id`.

## Sign in

| Endpoint | What it does |
|---|---|
| `GET /v1/auth/session` | Whether sign-in is on, how to sign in, who's signed in and their org. See below. |
| `GET /v1/auth/login/{provider}?next=/path` | Redirects to GitHub or Google with `state` and a PKCE challenge (kept in a 10-minute HttpOnly cookie). `404` for a provider that isn't set up, or `email` (it doesn't redirect). |
| `GET /v1/auth/callback/{provider}` | Where the provider sends the browser back. Checks `state`, signs in and redirects to `next`; on failure, to `/sign-in?error=…`. Refused: no verified email, or a domain outside `SOPOPS_ALLOWED_EMAIL_DOMAINS`. |
| `POST /v1/auth/email` | `{email, next?}` → `202 {message}`: emails a sign-in link. See below. |
| `POST /v1/auth/email/confirm` | `{token}` (the part after `#`) → `{...session, next}`, with the session cookies set. Signs in as the address, the same way as GitHub or Google (the first user owns the org, then invites and allowed domains; the same verified email is the same user). Uses up the link and the address's other open links. `404` if it's unknown, used or expired. |
| `POST /v1/auth/password/login` | `{email, password, next?}` → `{...session, next}`, with the session cookies set. `401 "That email and password don't match."` for a wrong password or an unknown address alike. `429` past 10 failed tries per address or 30 per client IP in 15 minutes. |
| `POST /v1/auth/password/first-account` | `{email, name?, password}` → `{...session, next}`: only while nobody has an account (else `409`). The account owns the org. Passwords: 10 to 256 characters, not the email address (`400`). |
| `POST /v1/auth/password/email` | `{email}` → `202 {message}`: emails a link that sets a password. See below. |
| `POST /v1/auth/password/link` | `{token}` → `{email, has_account, expires_at}`: what a password link is for. `404` if it's unknown, used or expired. |
| `POST /v1/auth/password/set` | `{token, password, name?}` → `{...session, next}`: sets the password (making the account, for an emailed sign-up link), uses up the link and the address's other open ones, ends the user's other login sessions, and signs in. Links work once, for an hour. |
| `POST /v1/auth/password/change` | `{current_password, password}` → `204`. `401` if the current password is wrong; `400` for an account with no password. The user's other login sessions end. |
| `POST /v1/auth/logout` | Ends the session (`204`) and clears the cookies. |
| `PUT /v1/auth/org` | `{org_id}`: work in another org you're in. |
| `POST /v1/auth/sign-up` | `{name?, email, password, team}` → `{...session, next}`. See below. |

**`GET /v1/auth/session`** returns `{required, providers: [{id, label, redirects}], user, org, orgs, first_account, password_email, sign_up, open_signup, platforms}`:

| Field | |
|---|---|
| `required` | Whether sign-in is on. |
| `providers` | How to sign in. `email` only with SMTP set up or `SOPOPS_EMAIL_DEV=1`, and `password` whenever sign-in is on. |
| `user` | Who's signed in, with `user.has_password`. |
| `org`, `orgs` | The org they're working in (`org` is null with no membership), and all of theirs. Each has `workspace`: the team's workspace (the one changed most recently, if it has several; null until it has one). |
| `first_account` | Nobody has an account yet. |
| `password_email` | "Forgot password?" can email a link. |
| `sign_up` | People at `SOPOPS_ALLOWED_EMAIL_DOMAINS` can make an account by asking for that link. |
| `open_signup` | `SOPOPS_OPEN_SIGNUP` is on, so anyone can sign up at `POST /v1/auth/sign-up`. |
| `platforms` | `{selected, shown}` for the org you're working in (with sign-in off, the first org's). `selected` is what the team picked (`livekit`, `elevenlabs`, `vapi`, `other`), null until someone picks. `shown` is the platforms the UI shows: all three when none was picked. |

**Email sign-in links** (`POST /v1/auth/email`) go to `<SOPOPS_PUBLIC_URL>/sign-in/email#<token>` and work once, for 15 minutes. The answer is the same whether a link went or not. None goes to a domain outside `SOPOPS_ALLOWED_EMAIL_DOMAINS`, or past 5 per address or 20 per client IP in 15 minutes. `400` for something that isn't an address; `404` when email sign-in is off. With `SOPOPS_EMAIL_DEV=1` the link is logged at INFO instead of sent.

**Password links** (`POST /v1/auth/password/email`) go to `<SOPOPS_PUBLIC_URL>/set-password#<token>`. They're sent for an address with an account, or, to make one, at `SOPOPS_ALLOWED_EMAIL_DOMAINS`. The answer is the same either way, and nothing is sent past 5 per address or 20 per client IP in 15 minutes. `404` without email set up.

**Open sign-up** (`POST /v1/auth/sign-up`) works only with `SOPOPS_OPEN_SIGNUP` on (else `404`). It makes the account and a new team it owns, with its one workspace, and works in it. `409` for an email that has an account (sign in instead), for one an open invite or a team's email domains are waiting for, and on a fresh install (use `first-account`). It counts against the password rate limits (`429`).

## Join a team

| Endpoint | What it does |
|---|---|
| `POST /v1/auth/invites/lookup` | `{token}` → `{org_id, org_name, email, role, expires_at, accepted, has_account}`. `404` once it's expired, withdrawn or used by someone else. |
| `POST /v1/auth/invites/join` | `{token, name?, password}` → `{...session, next}`: makes the account with a password and joins. Only for an email with no account yet (else `409`: sign in instead). |
| `POST /v1/auth/invites/accept` | `{token}`: join, when you're signed in with the invite's email (else `403`). |
| `POST /v1/auth/join/lookup` | `{token}` (from a join link, `<SOPOPS_PUBLIC_URL>/join#<token>`) → `{org_id, org_name, role, expires_at, member, needs_platforms}`. `404` once it's expired or turned off. |
| `POST /v1/auth/join` | `{token, name?, email, password}` → `{...session, next}`: makes the account with a password, joins the link's team with its role, and works in it. Only for an email with no account (else `409`: sign in, then join). Counts against the password rate limits (`429`). |
| `POST /v1/auth/join/accept` | `{token}` → the session: join the link's team, signed in, and work in it. Your other teams stay as they are; joining one you're in changes nothing. |

## Manage a team

| Endpoint | What it does |
|---|---|
| `POST /v1/orgs` | `{name}` → `201 {org, workspace}`: a new team you own, with its one workspace (named after it: `northwind`, or `northwind-2` when taken), and you work in it now. |
| `POST /v1/org/setup` | `{team}` → `{org, workspace}`: names the org after the team (and its slug, when free), and makes its workspace unless it has one. The first run calls it right after `first-account`. |
| `PATCH /v1/org/platforms` | `{platforms: [...]}` (`livekit`, `elevenlabs`, `vapi`, `other`) → `{selected, shown}`. Anyone in the team makes the first pick; after that, only owners and admins (`403`). With sign-in off, the first org's. |
| `GET /v1/org` | The org you're working in: `{id, name, slug, allowed_domains, role, sends_email}`. `sends_email`: SMTP is set up (and dev mode off), so invites are emailed. |
| `PATCH /v1/org` | `{name?, allowed_domains?}`. People with a verified email at an allowed domain join as members when they sign in. |
| `GET /v1/org/join-link` | The team's working join link, `{id, role, joined, created_at, expires_at}`, or null. Its address was shown once. |
| `POST /v1/org/join-link` | `{role?: member or admin, days?: 1 to 90, default 7}` → `201` with `link`, shown once. See below. |
| `DELETE /v1/org/join-link` | Turn it off (`204`). |
| `GET /v1/org/members` | `[{user_id, email, name, avatar_url, role, joined_at, last_login_at}]`. |
| `POST /v1/org/members` | `{emails: [...], role?}` (up to 100) → `[{email, status, user_id?, invite?}]`. See below. Only an owner adds owners (`403`). |
| `PATCH /v1/org/members/{user_id}` | `{role}`: `owner`, `admin` or `member`. Only an owner makes or changes an owner (`403`); the last owner can't be demoted (`409`). |
| `DELETE /v1/org/members/{user_id}` | Remove them. They can still sign in, but see nothing of the org. Only an owner removes an owner, and never the last one (`409`). |
| `POST /v1/org/members/{user_id}/password-link` | `201 {email, link, expires_at}`: a link, `<SOPOPS_PUBLIC_URL>/set-password#<token>`, shown once, that sets their password. Only an owner makes one for an owner (`403`). Someone also in an org where you're not an owner or admin gets `403`. `sopops reset-password <email>` prints the same kind of link. |
| `GET /v1/org/invites` | Open invites: `[{id, email, role, created_at, expires_at}]`. |
| `POST /v1/org/invites` | `{email, role?}` → `201` with `link` and `emailed`. See below. |
| `POST /v1/org/invites/{id}/resend` | A new link for an open invite, good for 7 more days, emailed again with SMTP. The old link stops working. Same shape as above. |
| `DELETE /v1/org/invites/{id}` | Cancel it: its link stops working. |

**Join links** (`POST /v1/org/join-link`): anyone who opens one can make an account, or sign in, and join with its role until it expires. The team's old link stops working. Stored as a hash.

**Adding members** (`POST /v1/org/members`) returns a `status` per email:
- `added`: they have an account and joined now;
- `invited`: `invite` is the new invite with its `link` and `emailed`. They join when they sign in with that email, any way;
- `already`: a member.

**Invites** (`POST /v1/org/invites`) return `link`, `<SOPOPS_PUBLIC_URL>/invite#<token>`, shown once. With SMTP set up the invite is emailed (`<name> invited you to <org> on sopops`). It works once, for 7 days, for whoever signs in with that email (signing in accepts it), and replaces the address's open invite.

**Who joins:** the first person to sign in on a fresh install owns the existing org; after that, invites and allowed domains decide. With sign-in off, `/v1/org/*` answers `409` (there are no members).

## Tokens

There's one kind of workspace token, with scopes. Tokens are stored as a sha256 hash and shown once, when made. Send them as `Authorization: Bearer <token>`. A missing, unknown, expired or revoked token (or one for another workspace) gets `401`; a valid token without the scope gets `403`.

| Scope | What it allows | Made |
|---|---|---|
| `ingest` | `POST /v1/workspaces/{ws}/sessions`. | Settings → Tokens, or `POST /v1/workspaces/{ws}/tokens`. |
| `runtime` | Fetching instructions (`/v1/workspaces/{ws}/agents/{agent}`, `/v1/workspaces/{ws}/prompt`, `/v1/workspaces/{ws}/sops/{id}`), `POST /v1/workspaces/{ws}/runtime/served`, and the call-start webhooks as a bearer. | Settings → Tokens, or `POST /v1/workspaces/{ws}/tokens`. |
| `config` | Reading and editing the forms (`/v1/workspaces/{ws}/config/*`): agents, shared instructions, procedures, groups and settings. Saves are drafts; it can't publish a release. For a coding agent that keeps SOPs up to date from a repo. | Settings → Tokens, or `POST /v1/workspaces/{ws}/tokens`. |
| `onboarding:write` | The coding agent's `/v1/onboarding/*` endpoints, for one import, for 2 hours. | By `POST /v1/workspaces/{ws}/imports/agent` only. |

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/tokens` | `[{id, name, prefix, scopes, import_id, created_at, expires_at, revoked_at, active, ...}]`, newest first, revoked ones included. Onboarding tokens only with `?imports=true`. |
| `POST /v1/workspaces/{ws}/tokens` | `{name?, scopes?: ["ingest", "runtime", "config"], expires_in_days?}` (default: both scopes, never expires). `201` with the token, shown once. |
| `DELETE /v1/workspaces/{ws}/tokens/{id}` | Revoke it: it stops working at once, and the row is kept with `revoked_at`. |

Fetching instructions stays open while the workspace has no unrevoked `runtime` token, for local use, unless sign-in is on. An expired one still closes them. A database from before tokens had scopes keeps each workspace's ingest token, with scopes `ingest` and `runtime`.

## AI keys and models

Each team's own TypeSafe key, provider key and models, over the deployment's defaults (the environment variables). Each value reports its `source`: `team`, `default`, or null when neither has one.

Keys are tried before they're saved, encrypted with `SOPOPS_SECRET_KEY` (`503` without it) and never returned. A key or model that doesn't work is a `422` that says why. Changing them needs an owner or admin.

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/ai` | What applies: `typesafe`, `provider`, `fast`, `reasoning` and `decision`, each with its `source`; `ready`; and the `providers` to pick from. See below. |
| `PUT /v1/workspaces/{ws}/ai/typesafe` | `{key}`. Tried with one small Jev decision. |
| `PUT /v1/workspaces/{ws}/ai/provider` | `{provider, key?, api_base?}`: `anthropic`, `openai`, `gemini`, `other`, or `sopops` where offered. Without `key`, the deployment's key for that provider. Returns the `models` the key can use and a `suggested` pair (none for `other`). Switching providers clears the team's models; picking `sopops` sets them to `sopops/sopops-fast` and `sopops/sopops-reasoning`. |
| `GET /v1/workspaces/{ws}/ai/models` | The models the team's provider key can use, and the suggested pair. |
| `PUT /v1/workspaces/{ws}/ai/models` | `{fast, reasoning}`, LiteLLM model strings. Each gets one short call first. |
| `GET /v1/workspaces/{ws}/ai/estimate?fast=&reasoning=&calls=` | What checking calls would cost with these models. See below. |
| `DELETE /v1/workspaces/{ws}/ai/{what}` | Back to the default: `typesafe`, `provider` (its key, base URL and the models) or `models`. |

**`GET /ai` details.**
- `decision` is the deployment's decision model (`SOPOPS_MODEL_DECISION`; `sopops/sopops-decision` on a hosted sopops), which answers decisions when there's no TypeSafe key. It's null when the team uses its own provider.
- `ready` means calls get checked: decisions (a TypeSafe key or the decision model) and both models are set.
- `providers` includes `sopops` only on a deployment with a model gateway (`SOPOPS_LM_URL`). Its models are `sopops/sopops-decision`, `sopops/sopops-fast` and `sopops/sopops-reasoning`.

**`GET /ai/estimate`** takes `fast` and `reasoning` (model strings, required) and `calls` (calls a month, default 25,000, 0 to 100,000,000). Decisions are priced as Jev when the team has a TypeSafe key or the deployment turns Jev on, else as the fast model. It returns:

| Field | |
|---|---|
| `basis`, `basis_calls` | `yours`: the team's average over its last 30 days, once it has 30 checked calls, with how many it covers. `typical`: a typical call. |
| `calls_per_month` | As asked. |
| `per_call`, `per_month` | `{low, high}` in USD, at list prices, before caching or batch discounts. Unpriced models are left out. |
| `roles` | Per role (`decision`, `fast`, `reasoning`): `model`, `input_tokens` and `output_tokens` per call, `input_price` and `output_price` per million tokens (null when no price is on file), and `low` and `high` per call. |
| `unpriced` | Models with no price on file, left out of the totals. |

Prices come from sopops's table and `SOPOPS_MODEL_PRICES` ([Model spend](insights.md#model-spend)), then LiteLLM's bundled price list.
