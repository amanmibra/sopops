# GitHub

Connect a workspace to a repository, keep its sopc project in sync both ways, and set up the GitHub App. The guide is [Instructions in git](../git.md).

A workspace's repository is the sopc project at `path` on `base_branch`. It authenticates in one of two ways:
- **The GitHub App's installation** (`auth: "app"`): installation tokens minted from the App's JWT, cached until shortly before they expire.
- **A fine-grained personal access token** (`auth: "token"`) with Contents and Pull requests: read and write, and Metadata: read. It's encrypted with `SOPOPS_SECRET_KEY` and never returned.

| GitHub answer | What you get |
|---|---|
| A refusal: a wrong or expired token, missing access, no such repository, branch or `sopc.yaml` | `400`, with what to fix. |
| Its rate limit | `429`. |
| Down or unreachable | `502`. |

## Connect and sync

| Endpoint | What it does |
|---|---|
| `POST /v1/workspaces/{ws}/repo` | Connect a repository. See below. `503` without `SOPOPS_SECRET_KEY`. |
| `GET /v1/workspaces/{ws}/repo/project` | What the folder holds at the branch's head, compiled with sopc, changing nothing: `{state: missing\|invalid\|valid, sha, files, agents, issues}`. `missing`: no folder, or no `sopc.yaml` in it. `invalid`: sopc can't build it (`issues` has its errors). |
| `GET /v1/workspaces/{ws}/repo` | The connection, or `null` if none is connected. See below. |
| `DELETE /v1/workspaces/{ws}/repo` | Forget the repository and its token. The files stay. |
| `POST /v1/workspaces/{ws}/repo/pull` | `?author=`: make head exactly the folder at the branch's head, as drafts (`build/` skipped). Returns `{sha, files, changed, unchanged}`. The same commit with head unchanged since is a no-op. `422` (sopc's issues) if it doesn't compile. |
| `POST /v1/workspaces/{ws}/repo/sync` | `?author=`: bring the folder at the branch's head into drafts, merging both ways. See below. |
| `GET/POST /v1/workspaces/{ws}/repo/conflict` | `?path=`: a sync conflict's `base`, `ours` and `theirs`. Post `{path, take: mine\|github\|edited, content?}` to settle it. |
| `POST /v1/workspaces/{ws}/repo/send` | Open or update the sync pull request (branch `sopops/sync`) with the drafts' differences from the branch's head. See below. |

**Connecting** (`POST /repo`) takes `{owner, repo, token?, installation_id?, base_branch?: "main", path?: "sops", require_project?: true}`.
- With `installation_id`, it signs in through the App: an installation made from this workspace, else `400`.
- It checks that the token reads the repository and the branch, and that `path` on `base_branch` has a `sopc.yaml`. `require_project: false`, as onboarding sends, skips that last check. Then it saves.
- Without `token`, the stored one is kept.

**The connection** (`GET /repo`) is `{owner, repo, base_branch, path, auth: token|app, installation_id, token_hint, url, pulled_sha, pulled_at, broken, sync_error, webhook_at, conflicts, sync_pr, ...}`. `pulled_sha` is the commit last synced. `broken` says why the connection stopped working: the App uninstalled or suspended, or the repository removed from it. Reading it polls GitHub in the background when due, the fallback for webhooks.

**Syncing** (`POST /repo/sync`) compares against the commit synced last:
- changed only on GitHub: written;
- changed both ways: merged (`sopops.merge`);
- doesn't merge: left alone and listed in `conflicts`.

It returns `{sha, changed, merged, conflicts, unchanged, error}`. `error` is set when GitHub's files don't build with the drafts, and then nothing is written. Webhooks (`push`) and polling run the same sync, once a connection has been synced.

**Sending** (`POST /repo/send`) syncs first. It answers `409` with nothing to send, with unresolved conflicts, or if the connection was never synced. It returns `{url, files, updated}`. When the pull request merges, the drafts are synced back, writing nothing new.

Repository events go to the sessions stream (`/v1/workspaces/{ws}/sessions/stream`) as `event: repo`, with `{topic: "repo", kind: synced|conflict|sync_error|sync_pr|pr_merged|resolved|broken, ...}`.

## The GitHub App

| Endpoint | What it does |
|---|---|
| `GET /v1/github/app` | `{configured, source: env\|db, slug, name, install_url, can_create, problem}`. Never the key or secret. |
| `GET /v1/workspaces/{ws}/github/install?return_to=` | `{url}`: GitHub's install page with a signed `state`. GitHub returns to `/v1/github/setup`, which lets the workspace use the installation and redirects to `return_to?github_installation=<id>`. |
| `GET /v1/workspaces/{ws}/github/installations`, `/v1/workspaces/{ws}/github/installations/{id}/repositories` | The installations this workspace may use, and the repositories each can reach. |
| `POST /v1/github/app/manifest` | `{public_url, return_to, organization?, webhook_url?}`: the form to post to GitHub to create an App, with permissions and events filled in. GitHub returns to `/v1/github/app/manifest/callback`, which stores its id, key and webhook secret (encrypted). |
| `POST /v1/github/webhook` | GitHub's webhooks. See below. |

**Webhooks** get `401` unless `X-Hub-Signature-256` matches the webhook secret, and each `X-GitHub-Delivery` is handled once.
- `push` syncs.
- `pull_request` closed updates suggestions, the onboarding import and the sync pull request at once.
- `installation` and `installation_repositories` mark connections broken or working again.

## Pull requests from code

Suggestions open pull requests in code. `sopops.github.open_pull_request(store, workspace, files, title, body)` commits `files` (paths relative to the project's folder; `None` deletes) to a new branch `sopops/<id>` off the base branch's head (blobs, tree, commit, ref), and opens a PR against the base branch. It returns the PR's URL. `pr_status(store, workspace, url)` is `open`, `merged` or `closed`.
