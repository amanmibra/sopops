# sopops API

Every endpoint. `{ws}` is the workspace id (each team has one). The OpenAPI spec at `/openapi.json` has every field; `/docs` lets you try them.

Agents, webhooks and coding agents use workspace tokens. Everything else needs a signed-in session once sign-in is on (passwords, GitHub, Google or email; see [docs/sign-in.md](docs/sign-in.md)). With sign-in off, only the tokens guard anything, so keep sopops on a private network.

Each operation's `operationId` is its route name (`list_workspaces`, `get_agent`, `config_put_procedure`), so generated clients have readable function names; the web UI's typed client (`web/src/client`) is generated this way. Paths outside `/v1` (and `/docs`, `/openapi.json`) serve the web UI.

## Errors

Errors that come from sopc return `422 {"valid": false, "issues": [...]}`. Each issue has sopc's `code`, `message`, `path` and `severity`, plus where it belongs in the forms:

| Field | |
|---|---|
| `kind` | `agent`, `instruction`, `procedure`, `group` or `settings` |
| `id` | Which one |
| `field` | As the `/config` endpoints name it, e.g. `blocks`, `steps` |
| `index` | Which entry of a list field |
| `text` | The problem in plain words, e.g. "Lists 'nope', which isn't a shared instruction, procedure or group." |

A `404` or `409` has `{"detail": "..."}`.

## Config: the forms

What the UI edits. Each item is stored as a sopc file (sopops writes it, then reads it back with `sopc export` to check it holds exactly what was sent), so git publishing and exports keep working. Saves are drafts: nothing reaches agents until a release. Ids can't be renamed yet.

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/config` | Every item at head: `{settings, agents, instructions, procedures, groups}`, each with its `version`. Instructions, procedures and groups have `used_by: [{agent, via}]` (`via`: the group the agent lists it through, or null). `422` with `old_format` issues if the workspace is in the sopc v0.0.8 format, or `removed_field` issues if it still sets `locked` (sopc v0.0.9); `POST /v1/workspaces/{ws}/migrate` converts either. |
| `GET/POST /v1/workspaces/{ws}/config/agents` | List agents; create one: `{id, platform, platform_id, context, blocks, variables, author?, note?}` (`409` if it exists). |
| `GET/PUT/DELETE /v1/workspaces/{ws}/config/agents/{id}` | One agent; create or replace it; delete it. |
| `POST /v1/workspaces/{ws}/config/agents/{id}/blocks` | `{block, author?, note?}`: add a shared instruction, procedure or group at the end of the agent's blocks. `409` if the agent already lists it or gets it through a group. |
| `DELETE /v1/workspaces/{ws}/config/agents/{id}/blocks/{block}` | Take a block out of the agent's blocks. `409` if the agent gets it through a group. |
| `POST /v1/workspaces/{ws}/config/agents/{id}/preview` | The agent's fields, unsaved: `{valid, issues, prompt, hash, blocks, release, released_prompt}`. Compiles with the rest of head; saves nothing. |
| `/v1/workspaces/{ws}/config/instructions[/{id}]` | Shared instructions: `{id, text}`. Deleting one takes it out of every agent and group that lists it. |
| `/v1/workspaces/{ws}/config/procedures[/{id}]` | Procedures (SOPs): `{id, name, goal, when, guidance, steps: [{text, tool, required}], never, warning_signs, delivery: prompt\|auto\|tool}`. New ones are written as Markdown; one that came from git keeps its format unless Markdown can't hold an edit, then it becomes YAML. Deleting one takes it out of every agent and group. |
| `/v1/workspaces/{ws}/config/groups[/{id}]` | Groups, kept in `sopc.yaml`: `{id, blocks}`. Deleting one lists its blocks in its place wherever it was used, so no agent's instructions change. |
| `GET/PUT /v1/workspaces/{ws}/config/settings` | `{variables, procedures_heading}`. |
| `GET /v1/workspaces/{ws}/config/{kind}/{id}/history` | Every version, newest first. Groups and settings list only the versions of `sopc.yaml` that changed them. |
| `GET /v1/workspaces/{ws}/config/{kind}/{id}/versions/{n}` | An older version, as fields. One that set `locked` is read as `sopc migrate` converts it; one saved in the v0.0.8 format comes back with `legacy: true` and its `content`. |

Instructions, procedures and groups share one set of names.

## Files

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces` | Workspaces and their current release. |
| `GET /v1/workspaces/{ws}/files` | Head: every file with its version. `?content=true` includes content (export to git). |
| `POST /v1/workspaces/{ws}/files` | `{changes: {path: content \| null}, author?, note?}`: edit or delete files atomically. Rejected if the result doesn't compile. |
| `PUT /v1/workspaces/{ws}/files` | `{files, author?, note?}`: replace head; files not listed are deleted. |
| `GET /v1/workspaces/{ws}/files/{path}?version=N` | A file at head or at a version. |
| `GET /v1/workspaces/{ws}/history/{path}` | Every version of a file. |

Paths under `build/` in uploads are ignored; absolute paths and `..` are rejected.

## Releases

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/draft` | Unpublished changes: files and `items` changed since the current release (each with its fields `before` and `after`), and agents added, changed, removed or unchanged, with the draft and released instructions, and `lint`: what `sopc lint` finds in the draft (duplicated or conflicting instructions; advisory, publishing isn't blocked). A release in an older format is compared as `sopc migrate` converts it. |
| `POST /v1/workspaces/{ws}/releases` | `{author?, note?}`: release head and serve it (returns the current release if nothing changed). |
| `GET /v1/workspaces/{ws}/releases`, `GET /v1/workspaces/{ws}/releases/{n}` | Release history; one release with every agent's instructions. |
| `POST /v1/workspaces/{ws}/releases/{n}/activate` | Serve an older release (roll back). |
| `POST /v1/workspaces/{ws}/publish` | `{files, author?, note?}`: replace + release in one call, for CI. |
| `POST /v1/workspaces/{ws}/migrate` | `{apply?, author?, note?}`: convert a workspace stored in an older sopc format with `sopc migrate`: the v0.0.8 format, or `locked` (v0.0.9), which it removes. Returns `{needed, plan, applied, changed}`; with `apply`, the converted files are written as a draft. |

## Serving

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/agents` | Agents in the current release (`?release=N` for another). |
| `GET /v1/workspaces/{ws}/agents/{agent}` | **Call start.** `{agent, platform_ref, release, hash, prompt, tools, tool_sops, components}`. `?release=N` pins a release (A/B). Logs a fetch. |
| `GET /v1/workspaces/{ws}/agents/{agent}/prompt` | Just the instructions as text, with `X-Sopc-Release` and `X-Sopc-Hash`. Logs a fetch. |
| `GET /v1/workspaces/{ws}/agents/{agent}/sops/{id}` | What a `get_sop` tool returns, for SOPs with `delivery: auto` or `tool`. |
| `GET /v1/workspaces/{ws}/fetches?agent=` | Which release and instructions hash each agent was served, newest first. |

`GET /v1/workspaces/{ws}/agents/{agent}`, `/v1/workspaces/{ws}/prompt` and `/v1/workspaces/{ws}/sops/{id}` take a token with the `runtime` scope ([Tokens](#tokens)) as `Authorization: Bearer <token>` once the workspace has one, and answer `401` without it (`403` for a token without the scope). A workspace without an unrevoked runtime token serves them openly (local mode).

`<agent>` is the sopc agent id (`tonys-pizza`) or its platform ref (`livekit:tonys-pizza`). `components` lists each file the instructions were built from, in order, at its version: `agent`, `instruction`, `sop`, plus `sopc.yaml` as `config` (`base` in releases made before sopc v0.0.9).

## Runtime delivery

How each agent gets its instructions: **pull** (the agent, or its platform's call-start webhook, fetches it) or **push** (sopops writes it into the agent on ElevenLabs or Vapi when a release goes live), or both.

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/runtime/delivery` | Every agent at head: `{agent, platform, platform_id, mode, modes}`. No setting means `pull`; LiveKit agents only pull. |
| `PUT /v1/workspaces/{ws}/runtime/delivery/{agent}` | `{mode: pull \| push \| both}`. `400` for a mode the platform doesn't allow. |
| `GET /v1/workspaces/{ws}/runtime/platforms` | For `elevenlabs` and `vapi`: `{api_key, webhook_secret}`, each `{set, hint, created_at}`. Never the values. |
| `PUT /v1/workspaces/{ws}/runtime/platforms/{platform}/api-key` | `{value}`: store the platform API key, encrypted with `SOPOPS_SECRET_KEY` (Fernet). `503` if that isn't set. |
| `PUT /v1/workspaces/{ws}/runtime/platforms/{platform}/webhook-secret` | `{value?}`: store a webhook secret; without a value one is made and returned once as `secret`. |
| `DELETE /v1/workspaces/{ws}/runtime/platforms/{platform}/<api-key \| webhook-secret>` | Remove it. |
| `POST /v1/workspaces/{ws}/runtime/platforms/{platform}/test` | `{platform_id?}`: read one agent with the stored key (a GET; default: the first agent at head on that platform). `{ok, platform_id, message}`. |
| `GET /v1/workspaces/{ws}/releases/{n}/pushes` | Each push-mode agent's latest push of the release: `{agent, platform, platform_id, status: ok \| unchanged \| error, version_id, error, attempts, at}`. |
| `POST /v1/workspaces/{ws}/releases/{n}/pushes/retry` | `{agents?}`: push again (default: the agents whose last push failed). `409` unless the release is live. |
| `POST /v1/workspaces/{ws}/runtime/served` | `{platform, call_id, agent, release}` with a runtime-scoped token: a client that fetched the instructions itself reports the call it served, so the session links to the release. |
| `POST /v1/workspaces/{ws}/runtime/elevenlabs/conversation-init` | ElevenLabs' conversation-initiation webhook (`?agent=`, `?release=` optional; default: the agent named by `agent_id` in the body, live release). Returns `{type: "conversation_initiation_client_data", dynamic_variables: {sopops_release, sopops_agent}, conversation_config_override: {agent: {prompt: {prompt}}}}`. |
| `POST /v1/workspaces/{ws}/runtime/vapi/assistant-request` | Vapi's server URL (`?agent=` needed if the release has more than one Vapi agent; `?release=` optional). For `assistant-request`: `{assistantId, assistantOverrides: {model, variableValues: {sopops_prompt, sopops_release, sopops_agent}, metadata}}`, where `model` is the assistant's own (read with the stored key, cached 5 minutes) with the system message replaced; without a key, only `variableValues` (use `{{sopops_prompt}}` as the system message). Other message types get `{}`. |

**Push.** Creating a release (`POST /v1/workspaces/{ws}/releases`, `/v1/workspaces/{ws}/publish`) or activating one pushes it to every push-mode agent, after the response. ElevenLabs: `GET /v1/convai/agents/{id}`, then `PATCH` with `{conversation_config: {agent: {prompt: {prompt}}}}`; an agent whose workflow has nodes besides start and end is refused, since its main prompt isn't all of its instructions. Vapi: `GET /assistant/<id>`, then `PATCH {model}` with every model field kept and the one system message replaced (added if there's none; refused if there are several). Instructions that are already live are recorded as `unchanged` without a write. `version_id` is ElevenLabs' `version_id` or Vapi's `latestVersion` when returned.

**Webhook authentication.** A request is accepted with the platform's webhook secret, as `Authorization: Bearer`, `X-Sopops-Secret` or `X-Vapi-Secret`, or as an HMAC-SHA256 signature: ElevenLabs' `ElevenLabs-Signature: t=…,v0=…` over `"{t}.{body}"` (at most 30 minutes old), or Vapi's `x-signature` (hex) and `x-timestamp` over `"{timestamp}.{body}"` (within 5 minutes). A token with the `runtime` scope as a bearer works too. A workspace with neither a webhook secret nor a runtime token accepts them unauthenticated (local mode), unless `SOPOPS_REQUIRE_WEBHOOK_SECRET` is true (set it when sopops is hosted): then they get `401`.

**Linking sessions.** Each serve, by a webhook or reported to `/v1/workspaces/{ws}/runtime/served`, is kept for 7 days keyed by the call id: Vapi's `call.id`, ElevenLabs' `conversation_id` or `call_sid`. A session ingested without a `release` takes it from there, matching `external_id`, `metadata.call_sid` or `metadata.phone_call.call_sid`.

## GitHub

A workspace's repository: the sopc project at `path` on `base_branch`. Auth is the GitHub App's installation (`auth: "app"`, installation tokens minted from the App's JWT and cached until shortly before they expire), or a fine-grained personal access token (`auth: "token"`; Contents and Pull requests: read and write; Metadata: read), encrypted with `SOPOPS_SECRET_KEY` and never returned. GitHub's refusals come back as `400` with what to fix (a wrong or expired token, missing access, no such repository, branch or `sopc.yaml`), its rate limit as `429`, and GitHub being down or unreachable as `502`.

| Endpoint | What it does |
|---|---|
| `POST /v1/workspaces/{ws}/repo` | `{owner, repo, token?, installation_id?, base_branch?: "main", path?: "sops", require_project?: true}`: with `installation_id`, sign in through the App (an installation made from this workspace, else `400`); check that the token reads the repository and the branch and that `path` on `base_branch` has a `sopc.yaml` (`require_project: false`, as onboarding sends, skips that last check), then save. Without `token` the stored one is kept. `503` without `SOPOPS_SECRET_KEY`. |
| `GET /v1/workspaces/{ws}/repo/project` | What the folder holds at the branch's head, compiled with sopc, changing nothing: `{state: missing\|invalid\|valid, sha, files, agents, issues}`. `missing`: no folder, or no `sopc.yaml` in it; `invalid`: sopc can't build it (`issues` has its errors). |
| `GET /v1/workspaces/{ws}/repo` | `{owner, repo, base_branch, path, auth: token\|app, installation_id, token_hint, url, pulled_sha, pulled_at, broken, sync_error, webhook_at, conflicts, sync_pr, ...}`; `null` if none is connected. `pulled_sha` is the commit last synced; `broken` says why the connection stopped working (the App uninstalled or suspended, or the repository removed from it). Reading it polls GitHub in the background when due (the fallback for webhooks). |
| `DELETE /v1/workspaces/{ws}/repo` | Forget the repository and its token; the files stay. |
| `POST /v1/workspaces/{ws}/repo/pull` | `?author=`: make head exactly the folder at the branch's head, as drafts (`build/` skipped). `{sha, files, changed, unchanged}`. The same commit with head unchanged since is a no-op; `422` (sopc's issues) if it doesn't compile. |

| `POST /v1/workspaces/{ws}/repo/sync` | `?author=`: bring the folder at the branch's head into drafts, against the commit synced last: changed only on GitHub is written, changed both ways is merged (`sopops.merge`), and what doesn't merge is left alone and listed in `conflicts`. `{sha, changed, merged, conflicts, unchanged, error}`; `error` when GitHub's files don't build with the drafts (nothing written). Webhooks (`push`) and polling run the same, once a connection has been synced. |
| `GET/POST /v1/workspaces/{ws}/repo/conflict` | `?path=`: a sync conflict's `base`, `ours` and `theirs`. Post `{path, take: mine\|github\|edited, content?}` to settle it. |
| `POST /v1/workspaces/{ws}/repo/send` | Open or update the sync pull request (branch `sopops/sync`) with the drafts' differences from the branch's head. Syncs first; `409` with nothing to send, unresolved conflicts, or never synced. `{url, files, updated}`. When it merges, the drafts are synced back, writing nothing new. |
| `GET /v1/github/app` | `{configured, source: env\|db, slug, name, install_url, can_create, problem}`. Never the key or secret. |
| `GET /v1/workspaces/{ws}/github/install?return_to=` | `{url}`: GitHub's install page with a signed `state`. GitHub returns to `/v1/github/setup`, which lets the workspace use the installation and redirects to `return_to?github_installation=<id>`. |
| `GET /v1/workspaces/{ws}/github/installations`, `/v1/workspaces/{ws}/github/installations/{id}/repositories` | The installations this workspace may use, and the repositories each can reach. |
| `POST /v1/github/app/manifest` | `{public_url, return_to, organization?, webhook_url?}`: the form to post to GitHub to create an App (permissions and events filled in). GitHub returns to `/v1/github/app/manifest/callback`, which stores its id, key and webhook secret (encrypted). |
| `POST /v1/github/webhook` | GitHub's webhooks: `401` unless `X-Hub-Signature-256` matches the webhook secret; each `X-GitHub-Delivery` is handled once. `push` syncs, `pull_request` closed updates suggestions, the onboarding import and the sync pull request at once, `installation`/`installation_repositories` mark connections broken or working again. |

Repository events go to the sessions stream (`/v1/workspaces/{ws}/sessions/stream`) as `event: repo` (`{topic: "repo", kind: synced|conflict|sync_error|sync_pr|pr_merged|resolved|broken, ...}`).

Pull requests are opened from suggestions, in code: `sopops.github.open_pull_request(store, workspace, files, title, body)` commits `files` (paths relative to the project's folder; `None` deletes) to a new branch `sopops/<id>` off the base branch's head (blobs, tree, commit, ref) and opens a PR against the base branch, returning its URL. `pr_status(store, workspace, url)` is `open`, `merged` or `closed`.

## Stateless

Nothing is stored.

| Endpoint | What it does |
|---|---|
| `POST /v1/validate`, `/v1/render`, `/v1/lint` | `{files}`: sopc's checks, each agent's full instructions, and duplicate or conflicting instructions. |
| `POST /v1/migrate` | `{files}`: a folder in an older sopc format (v0.0.8, or with `locked`), converted with `sopc migrate`. Returns `{migrated, plan, files}`. |

## Sign-in and the org

Sign-in is on once a login provider is set up (`SOPOPS_GITHUB_OAUTH_CLIENT_ID`/`_SECRET`, `SOPOPS_GOOGLE_CLIENT_ID`/`_SECRET`, or email: `SOPOPS_SMTP_HOST` and `SOPOPS_SMTP_FROM`, or `SOPOPS_EMAIL_DEV=1`; see the README's "Sign in"), and `SOPOPS_AUTH_REQUIRED=true|false` forces it either way. With it on, email and password sign-in is always offered (`true` with nothing else set up means passwords only). With it on, each endpoint is one of these (the table is `ROUTES` in `src/sopops/auth/access.py`, and a test checks every route is in it):

| Kind | Needs | Endpoints |
|---|---|---|
| open | nothing | `/v1/auth/session`, `/v1/auth/login/{provider}`, `/v1/auth/callback/{provider}`, `POST /v1/auth/email`, `POST /v1/auth/email/confirm`, `POST /v1/auth/invites/lookup`, `POST /v1/auth/invites/join`, `POST /v1/auth/password/{login,first-account,email,link,set}`, `POST /v1/auth/sign-up`, `POST /v1/auth/join/lookup`, `POST /v1/auth/join`, `/healthz`, `/docs`, `/openapi.json`, the web UI's files |
| token | the endpoint's own token, no login | `POST /v1/workspaces/{ws}/sessions` (and `/v1/workspaces/{ws}/open`, `/v1/workspaces/{ws}/events`, `/v1/workspaces/{ws}/close`): `ingest`. `POST /v1/workspaces/{ws}/runtime/served`: `runtime`. `/v1/onboarding/{session,kickoff,guide,extraction,project,progress,proposal,pull-request,pull-request/open,fidelity}`: `onboarding:write` |
| webhook | the sender's signature or secret, no login | `POST /v1/github/webhook`, `/v1/workspaces/{ws}/capture/livekit/webhook`, `/v1/workspaces/{ws}/capture/elevenlabs/post-call`, `/v1/workspaces/{ws}/runtime/elevenlabs/conversation-init`, `/v1/workspaces/{ws}/runtime/vapi/assistant-request` |
| token or session | a `runtime` token, or a login session | `GET /v1/workspaces/{ws}/agents/{agent}`, `/v1/workspaces/{ws}/prompt`, `/v1/workspaces/{ws}/sops/{id}`. With sign-in on these always need one, even before the workspace has a runtime token |
| signed in | a login session, with or without an org | `POST /v1/auth/logout`, `PUT /v1/auth/org`, `POST /v1/auth/invites/accept`, `POST /v1/auth/join/accept`, `POST /v1/auth/password/change` |
| admin | a login session as an owner or admin | `PATCH /v1/org`, `POST /v1/org/members`, `PATCH/DELETE /v1/org/members/{user_id}`, `POST /v1/org/members/{user_id}/password-link`, `GET/POST /v1/org/invites`, `POST /v1/org/invites/{id}/resend`, `DELETE /v1/org/invites/{id}`, `POST /v1/org/setup`, `GET/POST/DELETE /v1/org/join-link`, `POST /v1/github/app/manifest` and its callback |
| session | a login session as a member of the workspace's org | everything else, including `POST /v1/orgs` and `PATCH /v1/org/platforms` |

Without a session: `401 {"detail": "sign in first"}`. Signed in but in no org: `403`. A workspace in an org you're not in answers `404`. A new workspace goes into the org you're working in.

**Sessions.** The `sopops_session` cookie: HttpOnly, SameSite=Lax, Secure on https, a random token stored hashed. It lasts 30 days from the last use (`GET /v1/auth/session` renews the cookie) and ends at sign-out.

**CSRF.** With the session cookie, `POST`, `PUT`, `PATCH` and `DELETE` must send `X-CSRF-Token` with the `sopops_csrf` cookie's value (an HMAC of the session token, readable by the page, not by other sites); without it, `403`. Token and webhook endpoints take no cookie and need none. The open endpoints that sign in take JSON only, which another site can't make a browser send without passing CORS.

**Authors.** Signed in, the user is recorded wherever an author is (file versions, releases, verdicts, overrides, suggestions, imports, tokens): their name in the text field (`author`, `reviewer`, `created_by`), whatever the request sent, and their id in `author_id`.

| Endpoint | What it does |
|---|---|
| `GET /v1/auth/session` | `{required, providers: [{id, label, redirects}], user, org, orgs, first_account, password_email, sign_up, open_signup, platforms}`: whether sign-in is on, how to sign in, who's signed in (`user.has_password`) and the org they're working in (`org` is null with no membership). `providers` has `email` only with SMTP set up or `SOPOPS_EMAIL_DEV=1`, and `password` whenever sign-in is on. `first_account`: nobody has an account yet. `password_email`: "Forgot password?" can email a link. `sign_up`: people at `SOPOPS_ALLOWED_EMAIL_DOMAINS` can make an account by asking for that link. `open_signup`: `SOPOPS_OPEN_SIGNUP` is on, so anyone can sign up at `POST /v1/auth/sign-up`. Each org in `org` and `orgs` has `workspace`: the team's workspace (the one changed most recently, if it has several; null until it has one). `platforms`: `{selected, shown}` for the org you're working in (with sign-in off, the first org's): `selected` is what the team picked (`livekit`, `elevenlabs`, `vapi`, `other`), null until someone picks; `shown` is the platforms the UI shows, all three when none of them was picked. |
| `GET /v1/auth/login/{provider}?next=/path` | Redirects to GitHub or Google with `state` and a PKCE challenge (kept in a 10-minute HttpOnly cookie). `404` for a provider that isn't set up, or `email` (it doesn't redirect). |
| `GET /v1/auth/callback/{provider}` | Where the provider sends the browser back. Checks `state`, signs in and redirects to `next`; on failure, to `/sign-in?error=…`. Refused: no verified email, or a domain outside `SOPOPS_ALLOWED_EMAIL_DOMAINS`. |
| `POST /v1/auth/email` | `{email, next?}` → `202 {message}`: emails a sign-in link, `<SOPOPS_PUBLIC_URL>/sign-in/email#<token>`, that works once, for 15 minutes. The answer is the same whether a link went or not: none goes to a domain outside `SOPOPS_ALLOWED_EMAIL_DOMAINS`, or past 5 per address or 20 per client IP in 15 minutes. `400` for something that isn't an address; `404` when email sign-in is off. With `SOPOPS_EMAIL_DEV=1` the link is logged at INFO instead of sent. |
| `POST /v1/auth/email/confirm` | `{token}` (the part after `#`) → `{...session, next}`, with the session cookies set: signs in as the address, the same way as GitHub or Google (the first user owns the org, then invites and allowed domains; the same verified email is the same user). Uses up the link and the address's other open links. `404` if it's unknown, used or expired. |
| `POST /v1/auth/password/login` | `{email, password, next?}` → `{...session, next}`, with the session cookies set. `401 "That email and password don't match."` for a wrong password or an unknown address alike; `429` past 10 failed tries per address or 30 per client IP in 15 minutes. |
| `POST /v1/auth/password/first-account` | `{email, name?, password}` → `{...session, next}`: only while nobody has an account (else `409`). The account owns the org. Passwords: 10 to 256 characters, not the email address (`400`). |
| `POST /v1/auth/password/email` | `{email}` → `202 {message}`: emails a link, `<SOPOPS_PUBLIC_URL>/set-password#<token>`, that sets a password. For an address with an account, or (making one) at `SOPOPS_ALLOWED_EMAIL_DOMAINS`; the answer is the same either way, and past 5 per address or 20 per client IP in 15 minutes. `404` without email set up. |
| `POST /v1/auth/password/link` | `{token}` → `{email, has_account, expires_at}`: what a password link is for. `404` if it's unknown, used or expired. |
| `POST /v1/auth/password/set` | `{token, password, name?}` → `{...session, next}`: sets the password (making the account, for an emailed sign-up link), uses up the link and the address's other open ones, ends the user's other login sessions, and signs in. Links work once, for an hour. |
| `POST /v1/auth/password/change` | `{current_password, password}` → `204`. `401` if the current password is wrong; `400` for an account with no password. The user's other login sessions end. |
| `POST /v1/auth/logout` | Ends the session (`204`) and clears the cookies. |
| `PUT /v1/auth/org` | `{org_id}`: work in another org you're in. |
| `POST /v1/auth/invites/lookup` | `{token}` → `{org_id, org_name, email, role, expires_at, accepted, has_account}`. `404` once it's expired, withdrawn or used by someone else. |
| `POST /v1/auth/invites/join` | `{token, name?, password}` → `{...session, next}`: makes the account with a password and joins. Only for an email with no account yet (else `409`: sign in instead). |
| `POST /v1/auth/invites/accept` | `{token}`: join, when you're signed in with the invite's email (else `403`). |
| `POST /v1/auth/sign-up` | `{name?, email, password, team}` → `{...session, next}`: with `SOPOPS_OPEN_SIGNUP` on (else `404`), makes the account and a new team it owns, with its one workspace, and works in it. `409` for an email that has an account (sign in instead), for one an open invite or a team's email domains are waiting for, and on a fresh install (use `first-account`). Counts against the password rate limits (`429`). |
| `POST /v1/auth/join/lookup` | `{token}` (from a join link, `<SOPOPS_PUBLIC_URL>/join#<token>`) → `{org_id, org_name, role, expires_at, member, needs_platforms}`. `404` once it's expired or turned off. |
| `POST /v1/auth/join` | `{token, name?, email, password}` → `{...session, next}`: makes the account with a password, joins the link's team with its role, and works in it. Only for an email with no account (else `409`: sign in, then join). Counts against the password rate limits (`429`). |
| `POST /v1/auth/join/accept` | `{token}` → the session: join the link's team, signed in, and work in it. Your other teams stay as they are; joining one you're in changes nothing. |
| `POST /v1/orgs` | `{name}` → `201 {org, workspace}`: a new team you own, with its one workspace (named after it: `northwind`, or `northwind-2` when taken), and you work in it now. |
| `POST /v1/org/setup` | `{team}` → `{org, workspace}`: names the org after the team (and its slug, when free), and makes its workspace unless it has one. The first run calls it right after `first-account`. |
| `PATCH /v1/org/platforms` | `{platforms: [...]}` (`livekit`, `elevenlabs`, `vapi`, `other`) → `{selected, shown}`. Anyone in the team makes the first pick; after that owners and admins (`403`). With sign-in off, the first org's. |
| `GET /v1/org/join-link` | The team's join link that works, `{id, role, joined, created_at, expires_at}`, or null. Its address was shown once. |
| `POST /v1/org/join-link` | `{role?: member or admin, days?: 1 to 90, default 7}` → `201` with `link`, shown once. Anyone who opens it can make an account, or sign in, and join with that role, until it expires. The team's old link stops working. Stored as a hash. |
| `DELETE /v1/org/join-link` | Turn it off (`204`). |
| `GET /v1/org` | The org you're working in: `{id, name, slug, allowed_domains, role, sends_email}`. `sends_email`: SMTP is set up (and dev mode off), so invites are emailed. |
| `PATCH /v1/org` | `{name?, allowed_domains?}`. People with a verified email at an allowed domain join as members when they sign in. |
| `GET /v1/org/members` | `[{user_id, email, name, avatar_url, role, joined_at, last_login_at}]`. |
| `POST /v1/org/members` | `{emails: [...], role?}` (up to 100) → `[{email, status, user_id?, invite?}]`. `added`: they have an account and joined now. `invited`: `invite` is the new invite with its `link` and `emailed`; they join when they sign in with that email, any way. `already`: a member. Only an owner adds owners (`403`). |
| `PATCH /v1/org/members/{user_id}` | `{role}`: `owner`, `admin` or `member`. Only an owner makes or changes an owner (`403`); the last owner can't be demoted (`409`). |
| `DELETE /v1/org/members/{user_id}` | Remove them. They can still sign in, but see nothing of the org. Only an owner removes an owner, and never the last one (`409`). |
| `POST /v1/org/members/{user_id}/password-link` | `201 {email, link, expires_at}`: a link, `<SOPOPS_PUBLIC_URL>/set-password#<token>`, shown once, that sets their password. Only an owner makes one for an owner (`403`), and someone also in an org where you're not an owner or admin gets `403`. `sopops reset-password <email>` prints the same kind of link. |
| `GET /v1/org/invites` | Open invites: `[{id, email, role, created_at, expires_at}]`. |
| `POST /v1/org/invites` | `{email, role?}` → `201` with `link`, `<SOPOPS_PUBLIC_URL>/invite#<token>`, shown once, and `emailed`. With SMTP set up it's emailed ("<name> invited you to <org> on sopops"). It works once, for 7 days, for whoever signs in with that email (signing in accepts it), and replaces the address's open invite. |
| `POST /v1/org/invites/{id}/resend` | A new link for an open invite, good for 7 more days, emailed again with SMTP. The old link stops working. Same shape as above. |
| `DELETE /v1/org/invites/{id}` | Cancel it: its link stops working. |

Who joins: the first person to sign in on a fresh install owns the existing org; then invites and allowed domains. With sign-in off, `/v1/org/*` answers `409` (there are no members).

## Tokens

One kind of workspace token, with scopes. Tokens are stored as a sha256 hash and shown once, when made; send them as `Authorization: Bearer <token>`. A missing, unknown, expired or revoked token (or one for another workspace) gets `401`; a valid token without the scope gets `403`.

| Scope | What it allows | Made |
|---|---|---|
| `ingest` | `POST /v1/workspaces/{ws}/sessions`. | Settings → Tokens, or `POST /v1/workspaces/{ws}/tokens`. |
| `runtime` | Fetching instructions (`/v1/workspaces/{ws}/agents/{agent}`, `/v1/workspaces/{ws}/prompt`, `/v1/workspaces/{ws}/sops/{id}`), `POST /v1/workspaces/{ws}/runtime/served`, and the call-start webhooks as a bearer. | Settings → Tokens, or `POST /v1/workspaces/{ws}/tokens`. |
| `onboarding:write` | The coding agent's `/v1/onboarding/*` endpoints, for one import, for 2 hours. | By `POST /v1/workspaces/{ws}/imports/agent` only. |

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/tokens` | `[{id, name, prefix, scopes, import_id, created_at, expires_at, revoked_at, active, ...}]`, newest first, revoked ones included. Onboarding tokens only with `?imports=true`. |
| `POST /v1/workspaces/{ws}/tokens` | `{name?, scopes?: ["ingest", "runtime"], expires_in_days?}` (default: both scopes, never expires). `201` with the token, shown once. |
| `DELETE /v1/workspaces/{ws}/tokens/{id}` | Revoke it: it stops working at once; the row is kept with `revoked_at`. |

Fetching instructions stays open while the workspace has no unrevoked `runtime` token, for local use, unless sign-in is on; an expired one still closes them. A database from before tokens had scopes keeps each workspace's ingest token, with scopes `ingest` and `runtime`.

## Sessions

A session is one conversation an agent had: a voice call or a text chat. Sessions come from LiveKit (the SDKs' adapters, [Python](sdks/python) and [Node](sdks/node)), ElevenLabs or Vapi, or from OpenTelemetry, a file import or the API, and all of them use one canonical format.

| Endpoint | What it does |
|---|---|
| `POST /v1/workspaces/{ws}/sessions` | Ingest one session (below), with `Authorization: Bearer <token>` (scope `ingest`). An upsert on `(platform, external_id)`: `201 {id, created: true, updated: false, status, warnings}` for a new session; `200` with `updated: true` if its content changed; `200` with both false for an identical re-send, which changes nothing. A new or changed session runs `process_session`. `401` without a valid token for the workspace, `403` for one without the `ingest` scope. |
| `GET /v1/workspaces/{ws}/sessions` | Newest first by start time: `{items, next_cursor}`. Each has `status` and `result` (the worst procedure result, once checked). Filters: `agent` (sopc id, or the agent as sent), `channel`, `status`, `result`, `platform`, `since`, `until` (ISO 8601, on `started_at`), `release`, plus `cursor` (the previous page's `next_cursor`) and `limit` (default 50, max 200). Drill-down filters (what [Insights](#insights) links to): `procedure` (the router considered it), `procedure_result`, `rule` (any wording of it: its [lineage](#rule-lineage)) with optional `rule_status` (repeatable), `pattern` (a rule pattern includes its sub-patterns), `theme`, `finding` (true: with any finding; false: with none), `eligible` (the sessions Insights rates: checked or incomplete, with a result), `suppressed` (false: the finding filters leave suppressed findings out, as Insights does; true: only them). |
| `GET /v1/workspaces/{ws}/sessions/{id}` | One session with `turns`, `tool_calls`, `metadata` and `checks` ([SOP checks](#sop-checks)). |
| `GET /v1/workspaces/{ws}/sessions/stream` | Server-sent events: `event: session` with `data: {type: created \| updated, workspace, id, status}` each time a session is ingested or processed. A comment every 15s keeps it open. |

Ingest needs a token with the `ingest` scope ([Tokens](#tokens)); the rest of the API is open, as above.

### Streaming (optional)

A sender can also stream a session as it happens, then close it. Nothing is checked until it closes, so a session that streams ends up the same as one posted at the end. The SDKs do this with `stream=True` (Python `attach`) or `stream: true` (Node). It's the transport for runtime guidance in a later version: the pipeline has a hook for each batch of events (`pipeline.session_event`), which does nothing yet.

| Endpoint | What it does |
|---|---|
| `POST /v1/workspaces/{ws}/sessions/open` | `{platform, external_id, channel?, agent?, release?, version_ref?, started_at?, metadata?}`. Creates the session with status `open` (`201`), or returns the existing one (`200`). |
| `POST /v1/workspaces/{ws}/sessions/{platform}/{external_id}/events` | `{events: [{seq, type: turn \| tool_call, turn? \| tool_call?}]}`, up to 500. `seq` counts from 0 per session; a resent `seq` is ignored and events can arrive in any order (they're kept in `seq` order). Returns `{accepted, duplicates}`. Events for a session that has closed are ignored. `404` if it was never opened. |
| `POST /v1/workspaces/{ws}/sessions/{platform}/{external_id}/close` | `{ended_at?, partial?}`. Closes it with what streamed in and runs `process_session`. |

The full post to `POST /v1/workspaces/{ws}/sessions` still works at any point and wins: it replaces whatever streamed in. An open session with no events for `SOPOPS_SESSION_IDLE_MINUTES` (default 30) is closed as `incomplete` and checked, the next time that workspace opens a session or by the daily job. All three take the `ingest` token.

### The canonical format

```json
{
  "platform": "livekit",
  "external_id": "AJ_tp_8f2k1",
  "channel": "voice",
  "agent": "tonys-pizza",
  "release": 1,
  "version_ref": null,
  "started_at": "2026-10-15T23:04:10Z",
  "ended_at": "2026-10-15T23:04:40Z",
  "partial": false,
  "turns": [
    {"role": "agent", "text": "Thanks for calling Tony's Pizza! Pickup or delivery today?", "offset_s": 0.8},
    {"role": "user", "text": "Delivery please.", "at": 1792105454.2},
    {"role": "tool", "text": "check_delivery_zone", "name": "check_delivery_zone", "offset_s": 15.1}
  ],
  "tool_calls": [
    {"name": "check_delivery_zone", "arguments": {"address": "221 Mercer St"}, "result": {"in_zone": true}, "offset_s": 15.1}
  ],
  "metadata": {"room": "tp-delivery-8f2k1"}
}
```

| Field | |
|---|---|
| `platform` | Required. `livekit`, `elevenlabs` or `vapi`, or the transport: `otel`, `import`, `api`. |
| `external_id` | Required. The platform's id for the session: a LiveKit job id (room names get reused), an ElevenLabs conversation id, a Vapi call id. With `platform`, its unique key in the workspace. |
| `channel` | `voice` (default) or `text`. |
| `agent` | The sopc agent id, its platform ref (`livekit:tonys-pizza`) or its platform id on this platform. Matched against the given release (else the live one) and stored as `agent_id`; what was sent is kept as `agent_ref`. |
| `release` | The sopops release the agent ran on (`release` from `GET /v1/workspaces/{ws}/agents/{agent}` at session start). An unknown release is dropped with a warning; the session is still stored. |
| `version_ref` | The platform's own version of the agent: ElevenLabs `version_id`, Vapi `assistantVersion`. |
| `started_at`, `ended_at` | ISO 8601. `started_at` defaults to the first turn's time, else when it was received. |
| `partial` | The sender knows the transcript is incomplete; the status is then `incomplete`. |
| `turns` | In order. `role`: `agent`, `user`, `tool` or `system` (`assistant`, `bot`, `human`, `customer`, `function` and similar are mapped). `text`. Time as `offset_s` (seconds since the start) or `at` (ISO 8601, or Unix seconds or milliseconds), which becomes `offset_s`. Other fields (`interrupted`, `name`, …) are kept. |
| `tool_calls` | `{name, arguments, result, is_error, call_id, offset_s or at}`; other fields are kept. |
| `metadata` | Anything else, as an object. |

Processing (`process_session`) normalizes turns: canonical roles, `offset_s` from `at`, text trimmed, empty turns dropped (except tool turns), a turn that repeats the one before it dropped, sorted by time. Then it routes and checks the session ([SOP checks](#sop-checks)).

| Status | |
|---|---|
| `received` | Stored and normalized, not checked (yet). With no model configured it stays here, with `status_reason` saying so. |
| `routed` | The procedures that applied are known; their checks are running. |
| `checked` | Every procedure that applied was checked; `result` is set. |
| `excluded` | Nothing to check: no procedure applied, or every one that did was cut short (the session ended before it started or finished). `status_reason` says which. |
| `incomplete` | No turns, or the sender said the transcript is incomplete (or it was streamed and went idle). An incomplete session with turns is still checked, and keeps this status. (Not the procedure result `partial`.) |
| `failed` | Processing failed; `status_reason` says where (`normalize`, `route`, `check <procedure>`). |

### Importing a file

`sopops import FILE.jsonl --workspace WS` reads one session per line in this format (`-` reads stdin) straight into the database named by `DATABASE_URL`, with no token, and runs `process_session` for each new or changed one. Lines that don't parse are reported by line number and skipped; it exits 1 if any did. Importing the same file again changes nothing. [`examples/import-sessions/restaurant-sessions.jsonl`](examples/import-sessions) has ten made-up sessions for the test workspace.

## SOP checks

Each session is checked against the procedures its agent lists (directly or through a group) in the release it ran on, or the live release if it didn't say. Rules are identified by sopc's stable rule ids (`sopc export`, `lock.json`), so results line up across releases.

`GET /v1/workspaces/{ws}/sessions/{id}` returns `checks`, one per procedure the router considered:

| Field | |
|---|---|
| `procedure_id`, `procedure_name`, `procedure_version`, `release_id` | What was checked: the procedure file's version in that release. |
| `applicable` | Whether the procedure applied to the session. |
| `excluded_reason` | Applied, but the session ended before the procedure started or finished, so it isn't checked. |
| `route_reason` | The router's one-sentence reason. |
| `result` | `deviated`, `partial`, `followed` or `not_measurable`; null unless checked. |
| `checked_result` | The checker's own result. `result` leaves out [suppressed](#review-and-suggestions) findings; they differ only when one is suppressed. |
| `goal_reached`, `goal_reason` | Whether the procedure's goal was reached (null: unclear). |
| `steps_applicable`, `steps_done`, `nevers_violated` | Counts. Done includes out of order. |
| `model`, `prompt_version` | What produced it. |
| `rules` | `[{rule_id, kind, index, text, status, evidence: [{turn, quote, source}], reason, finding, finding_id, pattern_id, suppressed, override}]`, steps then nevers then warning signs. `turn` indexes `turns`; `source` is `transcript` or `tool_calls`. |

**Rule statuses.**

| Kind | Statuses |
|---|---|
| Step | `done`; `out_of_order` (done, but after a later step: the fewest steps that explain the order of their first evidence turns); `skipped` (the session went past where it belonged without it); `not_reached` (the session ended before it); `not_applicable` (its condition never arose). |
| Never | `violated` or `ok`. |
| Warning sign | `present` (the situation came up and was handled as the sign says), `unheeded` (it came up, the sign says what to do, and the agent didn't) or `absent`. A condition, never a pass or fail; `unheeded` is still "present" in every sign-present count. |

A step with `tool: x` and `required` is checked against the session's `tool_calls` when it has any: called is `done`, not called is `skipped` (unless the session never reached the step or it didn't apply). Without tool calls the transcript decides, and the evidence says so.

**Procedure result.**

| Result | When |
|---|---|
| `deviated` | A never rule was violated, a required step was skipped, or a step was skipped and the goal wasn't reached (or it's unclear). |
| `partial` | Otherwise: a step out of order or not reached, or a step that isn't required was skipped but the goal was reached anyway (leniency: the agent got there another way, so it's a gap, not a deviation). |
| `followed` | Every applicable step done, in order, and no never rule violated. |
| `not_measurable` | Nothing to judge: no applicable step and no never rule violated, or the agent never spoke. Left out of rates. |

A session's `result` is the worst of its procedures' (deviated, then partial, followed, not measurable).

**Findings** are the rule results with status `skipped`, `violated`, `out_of_order` or `unheeded` (`finding: true`): things the agent did or didn't do that someone may want to fix. `not_reached` isn't one (the session ended), and neither is a warning sign that was present and handled. A `violated` never rule always quotes the agent's own words; one the model flagged without such a quote is stored as `ok`, with the reason saying so. A skipped step that leniency kept from making its procedure deviated is still a finding. Findings are stored in their own table and grouped into [patterns](#patterns-and-themes).

Re-running a session replaces its results; a run for content that has since been re-sent writes nothing.

**How rules are checked.** Routing and each rule are decisions first: yes/no questions with a probability, answered by Jev when it's on (`TYPESAFE_API_KEY`, `sopops[jev]`) and by the `fast` model otherwise. Rules the decisions flag (`skipped`, `violated`, `unheeded`) or aren't sure about are reviewed by the `reasoning` model, and the rest keep the decision's status and evidence (`reason` starts with "Decided:"). `model` and `prompt_version` say which: the decision model and `decide-1`, or `<decision model>+<reasoning model>` and `decide-1+check-5`; with `SOPOPS_CHECK_MODE=review` the reasoning model judges every rule (`check-5`). Settings: `SOPOPS_DECISIONS` (JSON: bands as `[no, yes, decide]` for `route`, `responded`, `condition`, `step`, `never`, `warning`; `goal`; `review_statuses`), `SOPOPS_CHECK_REVIEW=off`. Thresholds fitted to a workspace's labeled sessions (`sopops.ai.programs.calibrate`) are stored per workspace and used for every question whose wording hasn't changed; they can widen a question's review band, never narrow it.

## Patterns and themes

Findings are grouped so the same mistake shows up once, with its count, instead of once per session.

- **Rule pattern** (`kind: rule`): every finding on one rule, in every wording it has had ([Rule lineage](#rule-lineage): its `rule_id` is the lineage id, the id of the rule's first wording). Created with the rule's first finding, and named then, once, by the reasoning model (`NameRulePattern`): what the agent did wrong, in a few plain words ("Volunteered a price or description", "Didn't read the order back"). Without a model it's the rule's text, trimmed to a short phrase; the next recluster (or `sopops patterns rename [--workspace WS] [--all]`) names those with the model. The UI shows "Step 3" or "Never rule 2" beside the name. Its `description`, and `rule.text`, are the rule as sessions ran it most recently (its lineage's current wording).
- **Failure modes** (`kind: sub`): once a rule has 8 findings, the reasoning model splits its findings into at most 5 named ways the agent failed it, plus an `other` sub-pattern (`kind: other`) for findings that fit none. New findings on a split rule are assigned to one of its failure modes, or to `other`, by a decision (Jev, or the fast model).
- **Reclustering**: a rule is split again when 25 findings arrived since its last clustering, or when `other` holds more than 25% of its findings (with at least 5 new since), plus a daily cron. New clusters are matched to the old ones by member overlap (Jaccard over finding ids, at least 0.3), so a matched failure mode keeps its id, name and verdicts. A failure mode that matches nothing is `retired`, not deleted; its verdict carries over to the new cluster that took most of its findings (Jaccard at least 0.3), unless that one has its own.
- **Themes**: the reasoning model groups a procedure's rule patterns by the phase of the session where they happen (opening, taking the order, closing...), in session order. Existing theme names and ids are kept whenever they still fit.

A finding's `pattern_id` is its most specific pattern. The pipeline assigns findings right after a session is checked (inline, or the Hatchet `sopops_patterns` workflow, one run at a time per workspace); due reclustering runs right after (inline) or in `sopops_recluster`. The daily cron is `sopops_recluster_daily` (`SOPOPS_RECLUSTER_CRON`, default `0 6 * * *`); without Hatchet, run `sopops recluster` from cron.

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/patterns` | `{items, clustered_at}`: rule patterns with their `sub_patterns`, counted in the window (default 365 days; same window filters as Insights), each with its `verdict`, `review_state` and `review_note` ([Review](#review-and-suggestions)) and `clustered_at`. `clustered_at` at the top: the workspace's last clustering. Filters: `procedure`, `rule`. |
| `GET /v1/workspaces/{ws}/patterns/{id}` | One pattern: the rule it's about, its failure modes (for a rule pattern), example phrasings and its sessions in the window. |
| `POST /v1/workspaces/{ws}/patterns/recluster` | `{procedure?, rule?, force?}`: assign unassigned findings, recluster what's due (every rule with 8+ findings with `force`), regroup themes. Calls the models, synchronously. `{assigned, reclustered, themed}`. |

`sopops recluster [--workspace WS] [--procedure P] [--rule R] [--force]` does the same from the command line.

## Review and suggestions

A reviewer gives a pattern (a rule pattern or a failure mode) a **verdict**, with an optional note and a free-text `reviewer`:

| Verdict | Means | Its findings | Suggestion |
|---|---|---|---|
| `real` | A real agent mistake. | Keep counting. | Change the instructions to prevent it. |
| `acceptable` | Fine in this situation. | Suppressed, now and later. | Add the exception to the rule. |
| `sop_wrong` | The instruction is wrong or outdated. | Keep counting. | Edit the rule. |
| `checker_wrong` | sopops misjudged. | Suppressed, and negative calibration labels. | None. |

Verdicts are appended, never edited: the latest is the pattern's verdict, the rest its `verdict_history`. A failure mode without one has its rule pattern's (`inherited: true`). An **override** says whether the checker got one finding right (`correct`); a wrong one is suppressed and a negative label, a right one overrides a `checker_wrong` pattern for that finding, and `acceptable` suppresses either way. Overrides are kept by session and rule, so they outlive a re-check.

**Suppressed** findings are kept, flagged (`suppressed` on a session's rule results) and left out of every Insights count and rate. A procedure check's `result` is rolled up again with its suppressed rules read as passed (`checked_result` keeps the checker's own), and the session's result is the worst of its checks, so a session whose only deviation is suppressed counts as followed. Insights shows the suppressed counts on their own (`suppressed` on tiles, scoreboard rows, ladder rows and patterns).

**Calibration labels** come from the same calls, per workspace and only from the database: every session with a reviewed finding on a procedure is a labeled session for its `check:<procedure>` thresholds, a finding judged wrong a negative label. A label on an earlier wording of a rule labels the rule as the fitted release words it (its lineage). `POST /v1/workspaces/{ws}/calibrate` (or `sopops calibrate --workspace WS [--procedure P] [--dry-run]`) fits them (`sopops.ai.programs.calibrate`) and returns, per procedure, the labels used and each question's threshold before and after. A procedure with fewer than 5 labeled sessions, or no rule with 3 labeled failures, is refused: `skipped` is `{code, message, have, need}`, with the code `too_few_labeled_sessions`, `too_few_failures` or `not_in_release`. A question that keeps its threshold says why the same way (`too_few_failures` on its rule, `too_alike`, `held_out`). It runs as a job (`sopops_calibrate` on Hatchet, the API waiting for it; else in the API process).

**Review state.** Each pattern has a `review_state`, with a `review_note` saying why:

| State | When |
|---|---|
| `needs_review` | No verdict; a verdict carried over by a recluster and not confirmed; a verdict older than a recluster that changed the pattern's findings materially (it kept less than half of them); or an `acceptable` or `checker_wrong` verdict older than the rule's current wording, with findings on that wording (it judged the old one, so they aren't suppressed until a verdict confirms it). |
| `open` | `real` or `sop_wrong`, with no suggestion applied for this verdict. |
| `fixing` | A suggestion for it was applied: a draft, or a pull request (open or merged). Waiting for the release with the edit (the first release whose file has the suggested edit, alone or merged with others; for a repo, the pull of the merge commit, then a publish) to be live, then for 10 sessions checked on the procedure on that release or later. `fix_release` is that release; once it's live, `fix_sessions` counts those sessions and `fix_sessions_needed` is 10 (also on `fixed`). |
| `fixed` | 10 such sessions, none with a finding in the pattern. |
| `regressed` | A finding in the pattern on a session that ran the fix's release or a later one (`review_note`: "Came back after release N"). The edit gives the rule a new sopc id; its lineage keeps the new wording's findings in the same pattern, so they count. |
| `closed` | `acceptable` or `checker_wrong`. |

The review queue lists `needs_review` and `regressed`; `state=open` lists verdicts waiting for a fix, and so on. A new verdict starts over.

A **suggestion** is an edit to the pattern's procedure, made by the reasoning model (`SuggestEdit`) from the procedure's file at head, the rule, the pattern, the verdict and note, and up to 5 of its findings. It must compile with the rest of head; one that doesn't is sent back once with sopc's issues, then `failed`. `plan.agents` lists every agent whose instructions change, with a diff. It's generated as a job (inline after the response, or the Hatchet `sopops_suggest` workflow). Status: `generating`, then `proposed` or `failed`; a proposal becomes `applied`, `pr_open` or `dismissed`. A pull request is followed on GitHub: every 15 minutes (`sopops_pr_status` on Hatchet, `SOPOPS_PR_STATUS_CRON`; without Hatchet, `sopops pr-status` from cron) and when the suggestion is read (at most once a minute). Merged makes it `merged`; closed without merging, `dismissed` with the note "PR closed". **Apply** opens a pull request when the workspace has a GitHub repo connection (`pr_open`, `pr_url`), else saves the file as a draft (`applied`), which shows in Unpublished changes and goes out with the next publish.

**When the procedure changed since a suggestion was written** (another suggestion was applied, or someone edited the file), its edit is merged into the file as it is now: a three-way line merge (`sopops.merge`) with the file it was written against as the base. Edits on different lines merge, neighbouring lines included; the same lines changed two ways, or two insertions at the same place, conflict. A clean merge is then checked like a proposal: it must compile with the rest of head and still define the procedure, or it's a conflict too. Each proposed suggestion has `freshness`, computed when it's read (in every list, the review queue and the pattern page too):

| `freshness` | Means | Apply |
|---|---|---|
| `current` | The file is the one it was written against. | Writes `new_text`. |
| `merges` | The file changed; the edit merges in and the result compiles. | Writes the merged file (`since_written.merged_text`) and records `merged_into_version`. A pull request gets the merged file too. |
| `conflict` | The same lines were changed, or the merged file doesn't compile. | `409`, saying why. **Rewrite** it instead. |

**Rewrite** (`POST /v1/workspaces/{ws}/suggestions/{id}/rewrite`, a proposed one) makes a new suggestion for the same pattern, generated like any other but from the file as it is now, with the edits made since the old one was written (a unified diff) given to the model to keep and build on (`changes_since`, prompt version `suggest-2`). The old one is `dismissed` with the note "Rewritten against the current text." and `rewritten_as`; the new one has `rewrite_of`. A new suggestion rather than the old one regenerated in place: each suggestion stays one proposal against one base file, so its history is kept as it was.

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/review` | `{items, total, counts, clustered_at}`: active leaf patterns (a rule pattern that isn't split, else its failure modes and `other`) in the review states asked for (`state`, repeatable; default `needs_review` and `regressed`), most sessions first; `counts` per state. Each with its theme, procedure name, rule text, 5 example findings (`finding_id`, `session_id`, `started_at`, `excerpt`, `evidence`), its `verdict` (or null), `review_state`, `review_reason` (`no_verdict`, `carried_over`, `reclustered`, `reworded`, `came_back`; null otherwise), `review_note`, `fix_release`, `fix_sessions`, `fix_sessions_needed`, `new_findings`, `suppressed_count` and its latest `suggestion` (or null). `limit` (default 50). |
| `POST /v1/workspaces/{ws}/calibrate` | `{procedure?, dry_run?}`: fit the check thresholds to the labels (above). `{workspace, dry_run, procedures: [{procedure_id, labels, negative_labels, saved, skipped, score_before, score, rules: [{rule_id, lineage_id, kind, index, text, field, before, after, skipped}]}]}`; `text` is the rule as it reads now (its lineage's latest wording). |
| `POST /v1/workspaces/{ws}/patterns/{id}/verdict` | `{verdict, note?, reviewer?, suggest?: true}`. Returns the verdict with `suppressed_count`, and `suggestion` when one was queued (`suggest` and a real, acceptable or sop_wrong verdict). |
| `POST /v1/workspaces/{ws}/findings/{id}/override` | `{correct, note?, reviewer?}`. Returns the override and whether the finding is now `suppressed`. |
| `GET /v1/workspaces/{ws}/patterns/{id}` | Also has `verdict`, `verdict_history`, `review_state`, `review_note`, `fix_release`, `fix_sessions`, `fix_sessions_needed`, `suppressed_count` (all time) and `suggestions`. |
| `GET /v1/workspaces/{ws}/patterns/{id}/regression` | JSON Lines, one per finding in the pattern: `session_id`, `external_id`, `transcript_ref` (the session's URL), `procedure_id`, `rule_id`, `rule_kind`, `rule_text`, `verdict`, `observed_status`, `expected_status`, `expect`. `expect: agent` (a real mistake): replay the conversation against the fixed instructions, and the rule should now pass. `expect: checker` (acceptable, sop_wrong, checker_wrong, or a finding overridden as wrong): re-check the same transcript, and the rule should come out `expected_status` (`done`, `ok`, or `present` for a warning sign). `409` without a verdict. |
| `POST /v1/workspaces/{ws}/patterns/{id}/suggest` | (Re)generate a suggestion for the pattern's verdict. Returns it, `generating`. `409` unless the verdict is real, acceptable or sop_wrong. |
| `GET /v1/workspaces/{ws}/suggestions?status=` | Newest first. Each with its dates: `created_at`, `applied_at` (saved as a draft), `pr_opened_at`, `merged_at` (when sopops saw it merged), and `released_in` and `released_at`: the first release whose file has its edit (the suggested text, or that merged with other edits). A proposed one has `freshness` (above). |
| `GET /v1/workspaces/{ws}/suggestions/{id}` | `old_text`, `new_text`, `rationale`, `plan` (`agents`: `agent`, `added`, `removed`, `diff`), `pattern`, `verdict`, `status`, `pr_url`, `error`, `merged_into_version`, `rewrite_of`, `rewritten_as`. A proposed one also has `since_written`: `state` (its `freshness`), `head_version`, `changes` (a unified diff of the file since it was written), `merged_text` (`merges`), `conflicts` (`conflict`: each part's `line` in the file now, and its `base`, `current` and `suggested` lines) and `issues` (`conflict`: sopc's errors with the merged file). |
| `POST /v1/workspaces/{ws}/suggestions/{id}/apply` | `{reviewer?}`. A pull request or a draft edit (above), merged with changes made since when they don't overlap. `409` when it isn't proposed or is in conflict. |
| `POST /v1/workspaces/{ws}/suggestions/{id}/rewrite` | `{reviewer?}`. A new suggestion against the procedure as it is now (above). Returns it, `generating`. `409` unless it's proposed. |
| `POST /v1/workspaces/{ws}/suggestions/{id}/dismiss` | `{note?, reviewer?}`. |

## Rule lineage

sopc derives a rule's id from its text, so a reworded rule (by hand, or by an applied suggestion) has a new id. Sessions and findings keep the exact rule id and wording they ran. sopops records which rule each reworded rule replaced (`rule_lineage`), and everything about a rule follows the chain: its rule pattern (keyed by the lineage id, the first wording's id), so verdicts, review states and failure modes; suggestions (made for the rule as it reads at head); Insights rows, trends, the rule page and release compare; the sessions filter `rule`; calibration labels.

**At publish** each procedure's rules in the release that was live are compared with the new release's. A rule whose id is in both continues. Changed rules are matched old to new, one to one, within one procedure and one kind (step, never rule, warning sign):

1. `suggestion`: an applied suggestion (a draft, or a pull request open or merged) whose edit is in the file published (its suggested file, or that merged with other edits) knows the rule it edited; its new wording is the new rule at that position (else the most similar new one).
2. `matched`: the new rule at the same position, if the two wordings' similarity is at least 0.35.
3. `matched`: the most similar remaining pair anywhere in the procedure, best first, if at least 0.5 (moved and reworded).

Similarity is token Jaccard over lowercase words, without a few function words (`a`, `the`, `to`, `you`...). On the test fixture's and the walkthrough's procedures, rewordings score 0.38 to 1 and two different rules of one procedure at most 0.29. An unmatched new rule is new, an unmatched old one removed. A wording that comes back (a revert) stays in its own lineage. Links are never overwritten by later publishes.

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/rules/{rule_id}/lineage?procedure=` | `{procedure_id, rule_id, lineage_id, previous_rule_id, how, rules: [{rule_id, previous_rule_id, how, similarity, release_id, text, from_release}]}`, oldest first. `procedure` only when the id is in more than one procedure. |
| `POST /v1/workspaces/{ws}/rules/{rule_id}/lineage` | `{previous_rule_id, procedure_id?}`: the rule replaced `previous_rule_id` (a `manual` link). Its findings join that rule's pattern; when both had a pattern they merge into the older one (failure modes, verdicts, whose latest then counts, and suggestions move over; its findings wait in `other` for the next recluster if it's split). `409` for a cycle. |
| `DELETE /v1/workspaces/{ws}/rules/{rule_id}/lineage?procedure=` | The rule is a new rule (kept as a `manual` row with no previous rule): its findings, and the later wordings', move to a rule pattern of their own, with no verdict. |
| `POST /v1/workspaces/{ws}/rules/lineage/rebuild` | Recompute the `matched` and `suggestion` links of every release against the one before it by number (manual rows stay), and move patterns to match. Idempotent. `{workspace, links, procedures}`. |

`sopops lineage rebuild [--workspace WS]` does the rebuild from the command line; migration 0013 ran it on every workspace when it added the table (it needs the sopc binary; without one it says to run the command).

## Insights

Every number comes from one module (`sopops.insights`) with one set of definitions, computed on the fly:

| Term | Definition |
|---|---|
| Window | `[start, end]` on the session's start time, both ends inclusive, at most 366 days. `window=24h\|7d\|30d\|90d\|365d` (default 7d) or `start` and `end`. The prior window is the same length right before it. |
| Eligible session | Status `checked` or `incomplete`, with a result. In-progress and failed sessions are counted, never rated; excluded sessions are counted separately. |
| Session outcome | The worst result of its procedure checks in scope (only the `procedure` filter's, if set). **Followed end to end**: followed. **Needs attention**: deviated. |
| Pass rate (procedure) | Checks followed / measurable checks (deviated, partial or followed); `not_measurable` checks are never rated. |
| Pass rate (rule) | Steps: done / (done + skipped + out of order). Never rules: ok / (ok + violated). `n` is that denominator; `not_reached` and `not_applicable` are shown as a share, never counted. Warning signs are never in a pass rate: they are present (`present` or `unheeded`) or absent. |
| Percentages | Whole numbers, rounded half up, with their `n`; null (shown "—") when the denominator is 0. Rate deltas are in points; count deltas in %. |
| Suppressed findings | A verdict or override says they aren't a problem ([Review](#review-and-suggestions)): left out of every count and rate, with procedure and session results rolled up without them; counted on their own as `suppressed`. |
| Rules | Counted by lineage ([Rule lineage](#rule-lineage)): a reworded rule's results add up with its earlier wordings', under the wording sessions ran most recently (`rule_id`, `text`). Rows have `lineage_id`, `wording_history` (`[{rule_id, text, from_release}]`, oldest first; `from_release` is the release that published the wording, else the first a session ran it on) and `reworded_in` (the release that brought the current wording, null if never reworded). |
| Comparisons | 95% Wilson interval per rate. A side with n < 10 is `thin` and never flagged; `regressed` / `improved` need a change of 5 points or more and intervals that don't overlap. Releases are compared by the release each session ran, never by date. |

All take the window filters plus `agent`, `procedure`, `release` (repeatable) and `channel`.

| Endpoint | What it returns |
|---|---|
| `GET /v1/workspaces/{ws}/insights/overview` | Tiles (current, prior, deltas): sessions checked, followed end to end, needs attention, excluded, procedure pass rate, goal rate. Trend per day (windows up to 31 days) or week, with release markers. Procedure scoreboard: checks, pass rate and delta, top failing rules, never violations, open patterns, excluded. |
| `GET /v1/workspaces/{ws}/insights/procedures/{id}` | Tiles, trend, the rule ladder (per rule: n, pass, fail and skip rates, not-counted share, warning-sign presence, findings, patterns), the release split (previous vs latest release in the window, per rule), warning-sign impact (pass rate with the sign present vs absent), and patterns by theme. |
| `GET /v1/workspaces/{ws}/insights/procedures/{id}/rules/{rule_id}` | The rule across its wordings (`rule_id` is any of them): `wordings` (each with `rule_id`, `from_release`, first and last seen, results), `reworded_in`, its pass rate overall, per release and per agent (with intervals), its patterns, and the 5 most recent findings with their quotes. |
| `GET /v1/workspaces/{ws}/insights/releases/compare?a=&b=` | Release B against A (default: the latest release with sessions in the window, and the one before): per procedure and per rule (by lineage), both rates with n and intervals, the delta in points (null under the minimum n), a flag, and whether the rule is new, removed or reworded (`rule_change: changed`, with A's wording in `a_text`, the release that brought B's wording in `reworded_in`, and every wording in `wording_history`). |
| `GET /v1/workspaces/{ws}/insights/report` | The review report: headline numbers (plus sessions with problems on more than one rule), then per procedure its themes, each with its patterns: the rule they're about, failure modes, example phrasings and the sessions. |

## Model spend

Every model call sopops makes is recorded in `model_calls`: the model, the input and output tokens it reported, and its estimated cost, for the workspace, the session it was about (routing, checks, the reviewer, assigning its findings) and its purpose: `route`, `check`, `review`, `cluster` (assigning findings, splitting and naming patterns), `theme`, `suggestion`, `calibrate`, `onboarding`. The text sent to models and their replies aren't stored. Calls are seen through DSPy's usage tracker, so every client path counts (a `dspy.LM`, native or through LiteLLM, and Jev); a cached answer costs nothing and isn't recorded.

Costs come from a price table in USD per million tokens (input, output): Jev at $0.042 input and free output, and the current Claude models at list prices. `SOPOPS_MODEL_PRICES` (JSON, `{"model": [input, output]}`) changes or adds entries. A model string matches its name with or without the provider, else the longest entry it starts with (dated snapshots). A model with no price has its tokens recorded and a null cost.

| Endpoint | |
|---|---|
| `GET /v1/workspaces/{ws}/usage?window=` | Over the window (`window`, or `start` and `end`; on when the call was made): `total` (`calls`, `input_tokens`, `output_tokens`, `cost_usd`, `unpriced_calls`), `by_purpose` and `by_model` (largest cost first), `sessions` and `per_session_usd` (the cost of calls about a session, per session), and `daily`, one row per UTC day. |
| `GET /v1/model-prices` | The price table (`source`: `default` or `env`), and each configured role's model with its price. |

A session's detail has its own `model_calls` and `cost_usd`.

## Onboarding

Importing agents' existing instructions, or an existing sopc project. Every way ends in a **proposal** (agents, shared instructions, procedures, groups, findings, and per-agent fidelity) that the user reviews and applies as drafts. Fidelity compiles each agent and compares it with its original instructions: `score` (the share of the original's sentences kept, from `sopc compare`), `missing`, `changed` and `reworded` sentences, `similarity` (word-level, 0 to 1), and `rules_verbatim` of `rules`: the steps, never rules and warning signs the full instructions state that appear in the original word for word (`not_verbatim` lists the others).

| Endpoint | What it does |
|---|---|
| `GET /v1/onboarding/capabilities` | `{llm, model, platforms}`: whether sopops can extract with its own model. |
| `POST /v1/onboarding/platform-agents` | `{platform: elevenlabs\|vapi, api_key}`: the agents the key can see. The key is used for this request only. |
| `GET/POST /v1/workspaces/{ws}/imports` | List imports; start one with sopops's model: `{sources: [{name, platform, platform_id?, prompt, tools}], platform?: {platform, api_key, agent_ids}}`. `503` if no model is configured. Runs in the background. |
| `POST /v1/workspaces/{ws}/imports/agent` | `{mode: hosted\|repo, server_url?}`: an onboarding token for Claude Code or Codex, shown once, plus `kickoff`: one command per agent (add the MCP server and start the agent on "call get_kickoff"), the Codex config as TOML, and recommended settings for each (`settings_claude_code`, `settings_codex`). Repo mode needs the workspace's GitHub repository (`400` without one, or when GitHub refuses the token): its folder is read first and recorded on the import (`repo`, `repo_branch`, `repo_path`, `repo_project`: `missing`, `invalid` or `valid`). Unless it's `valid`, the agent's kickoff tells it to open the onboarding pull request adding the project there, and to report it. |
| `POST /v1/workspaces/{ws}/imports/sopc` | `{files?, from_repo?, originals?}`: an existing sopc project (its files by path relative to the folder with `sopc.yaml`, `build/` ignored; or `from_repo: true` reads the workspace's GitHub repository), with each agent's original instructions optionally. `source: sopc`, ready at once: the proposal shows what the project holds and carries its `files`; fidelity is checked for every agent with an original, as for any proposal. `400` without files or `sopc.yaml`, `422` when sopc can't read it. Its content isn't edited here (`409`): change the project and import it again. |
| `PUT /v1/workspaces/{ws}/imports/{id}/originals` | `{originals: {agent: prompt}}`: add or replace agents' original instructions (an empty one removes them) and check fidelity again. |
| `GET /v1/workspaces/{ws}/imports/{id}` | Status (`waiting`, `running`, `ready`, `failed`, `applied`, `discarded`), token state, the proposal, and in repo mode the onboarding pull request (`pr_url`, `pr_state`: `open`, `merged`, `closed`; `pr_merged_at`). An open one is checked on GitHub first (at most once a minute), and by the `sopops_pr_status` cron (`sopops pr-status`): merged pulls the folder into drafts (`POST /v1/workspaces/{ws}/repo/pull`) and makes the import `applied`; if that pull fails, `error` says why. |
| `GET /v1/workspaces/{ws}/imports/{id}/events?after=N`, `GET /v1/workspaces/{ws}/imports/{id}/stream` | Progress messages; the stream is server-sent events. The coding agent's messages (`by: agent`) carry `client` (Claude Code, Codex) when its MCP client said which it is. |
| `PUT /v1/workspaces/{ws}/imports/{id}/proposal` | Save edits; fidelity is checked again. |
| `POST /v1/workspaces/{ws}/imports/{id}/findings/{finding}` | `{choice}`: `keep`, or the agent whose version every agent gets. |
| `GET /v1/workspaces/{ws}/imports/{id}/review` | The proposal as summaries, for any number of agents: `counts` (agents, shared blocks, variables, disagreements, undecided, agents with unique content, below 100% fidelity), `disagreements` (rule words that differ, from `sopc overlap`: majority against outliers, smallest minority first; then drift the model suggested beyond what overlap finds), `variables` (each × agent), `blocks` (who uses each proposed block, and who lacks text most agents have), `unique` (each agent's text no other has), `fidelity`. Group ids are sopc's, stable across runs. |
| `POST /v1/workspaces/{ws}/imports/{id}/review/{group}` | `{choice}`: a disagreement `keep` or `all:<agent>`; unique text `context` or `block` (a new shared instruction). Kept with the proposal by group id. |
| `PUT /v1/workspaces/{ws}/imports/{id}/variables/{name}` | `{name?, values?}`: rename a variable everywhere, or set agents' values. |
| `POST /v1/workspaces/{ws}/imports/{id}/apply` | Write the proposal as drafts (items with the same id are replaced); an imported sopc project writes exactly its files. Ends the token. |
| `POST /v1/workspaces/{ws}/imports/{id}/revoke`, `DELETE /v1/workspaces/{ws}/imports/{id}` | End the token (Finish); discard the import. |

With `Authorization: Bearer <onboarding token>` (scope `onboarding:write`, one workspace, 2 hours, stored hashed; `401` when unknown, expired or revoked, `403` without the scope). sopops-mcp also sends `X-Sopops-Client` and `X-Sopops-Client-Version`, its MCP client's clientInfo (e.g. `claude-code`, `2.1.3`). The first request with a token is recorded on the import (`agent_client`: name, version, label, `connected_at`) with a progress message such as "Claude Code 2.1.3 connected", once:

| Endpoint | What it does |
|---|---|
| `GET /v1/onboarding/session` | The workspace, import and mode the token is for; in repo mode the repository, branch, folder, what the folder held, and `pr_url`. |
| `GET /v1/onboarding/kickoff?server_url=` | `{instructions}`: the sopops-onboard skill and this session (what `get_kickoff` returns). |
| `GET /v1/onboarding/guide` | `{guide, proposal_schema, finding_schema}`: the extraction rules and the JSON schemas. |
| `POST /v1/onboarding/extraction` | `{prompts: [{name, platform, platform_id, prompt, tools}]}`: the extraction guide (`instructions`), each agent's instructions split into numbered units for the agent to classify, and `originals` by agent id. No model is called; nothing stored. |
| `POST /v1/onboarding/project` | `{proposal}`: the proposal as a sopc project's files (`{files, issues, validation}`), written and checked with sopc; nothing stored. |
| `POST /v1/onboarding/progress` | `{step, message}`: shown live on the import page. |
| `PUT /v1/onboarding/proposal` | `{proposal, originals, findings?, notes?}`: replaces the import's proposal; returns fidelity. |
| `POST /v1/onboarding/fidelity` | `{proposal, originals}`: fidelity only, nothing stored. |
| `GET /v1/onboarding/skill` | `{name, text}`: SKILL.md, for `sopops-mcp install-skill`. No token. |
| `POST /v1/onboarding/pull-request` | `{url}`: repo mode, the onboarding pull request the agent opened (`400` unless it's a pull request in the connected repository). sopops follows it from then on. |
| `POST /v1/onboarding/pull-request/open` | `{description?}`: repo mode through the GitHub App (`repo_auth: "app"` in the session): sopops opens the onboarding pull request itself from the pushed proposal, and follows it. `POST /v1/workspaces/{ws}/imports/{id}/pull-request` does the same from the UI. |
