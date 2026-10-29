# GitHub

Connect a workspace to a repository, keep its sop project in sync both ways, and set up the GitHub App. The guide is [Instructions in git](../git.md).

A workspace's repository is the sop project at `path` on `base_branch`. It signs in one of two ways:

| `auth` | Credential |
|---|---|
| `app` | The GitHub App's installation: tokens minted from the App's JWT, cached until shortly before they expire. |
| `token` | A fine-grained personal access token with Contents and Pull requests read and write, and Metadata read. Encrypted with `SOPOPS_SECRET_KEY` and never returned. |

| GitHub's answer | What you get |
|---|---|
| A refusal: a wrong or expired token, missing access, no such repository, branch or `sop.yaml` | `400`, with what to fix. |
| Its rate limit | `429`. |
| Down or unreachable | `502`. |

## Connect and sync

| Endpoint | What it does |
|---|---|
| `POST /v1/workspaces/{ws}/repo` | Connect a repository. Fields below. `503` without `SOPOPS_SECRET_KEY`. |
| `GET /v1/workspaces/{ws}/repo` | The connection, or `null` if none is connected. Fields below. Reading it polls GitHub in the background when due, the fallback for webhooks. |
| `DELETE /v1/workspaces/{ws}/repo` | Forget the repository and its token. The files stay. |
| `GET /v1/workspaces/{ws}/repo/project` | What the folder holds at the branch's head, compiled with sop, changing nothing: `{workspace, repo, base_branch, path, sha, state, files, agents, issues}`. `state` is `missing` (no folder, or no `sop.yaml` in it), `invalid` (sop can't build it; `issues` has the errors) or `valid`. |
| `POST /v1/workspaces/{ws}/repo/pull` | `?author=`: make head exactly the folder at the branch's head, as drafts (`build/` skipped). Returns `{workspace, sha, files, changed, unchanged}`. The same commit with head unchanged since is a no-op. `422` (sop's issues) if it doesn't compile. |
| `POST /v1/workspaces/{ws}/repo/sync` | `?author=`: bring the folder at the branch's head into drafts, merging both ways. Returns `{workspace, sha, changed, merged, conflicts, unchanged, error}`. |
| `GET/POST /v1/workspaces/{ws}/repo/conflict` | `?path=`: a sync conflict's `base`, `ours` and `theirs`. Post `{path, take: mine \| github \| edited, content?, author?}` to settle it. |
| `POST /v1/workspaces/{ws}/repo/send` | Open or update the sync pull request (branch `sopops/sync`) with the drafts' differences from the branch's head. Returns `{url, files, updated}`. |

```sh
curl -s -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/repo" \
  -d '{"owner": "acme", "repo": "voice-agents", "token": "github_pat_...", "base_branch": "main", "path": "sops"}'
```

```json
{
  "workspace": "harbor-qa",
  "owner": "acme",
  "repo": "voice-agents",
  "base_branch": "main",
  "path": "sops",
  "auth": "token",
  "installation_id": null,
  "token_hint": "...a1b2",
  "url": "https://github.com/acme/voice-agents/tree/main/sops",
  "pulled_sha": null,
  "pulled_at": null,
  "broken": null,
  "sync_error": null,
  "webhook_at": null,
  "conflicts": [],
  "sync_pr": null,
  "created_at": "2026-10-19T01:40:00Z",
  "updated_at": "2026-10-19T01:40:00Z"
}
```

### Connecting

`POST /repo` takes `{owner, repo, token?, installation_id?, base_branch?: "main", path?: "sops", require_project?: true}`.

- With `installation_id`, it signs in through the App: an installation made from this workspace, else `400`.
- It checks that the credential reads the repository and the branch, and that `path` on `base_branch` has a `sop.yaml`. `require_project: false`, as onboarding sends, skips that last check. Then it saves.
- Without `token` or `installation_id`, the stored credential is kept, so you can change the branch or path alone.

### Reading the connection

| Field | |
|---|---|
| `pulled_sha`, `pulled_at` | The commit last pulled or synced: the base of the next sync. |
| `token_hint` | The token's last characters, to tell tokens apart. Null for `app`. |
| `broken` | Why the connection stopped working: the App uninstalled or suspended, or the repository removed from it. |
| `sync_error` | Why the last sync from GitHub wrote nothing. |
| `webhook_at` | When GitHub last sent a webhook. While that is recent, polling backs off. |
| `conflicts` | `[{path, sha, deleted_on_github, at}]`: files changed both ways that don't merge. |
| `sync_pr` | `{url, state}` for the pull request that sends drafts to GitHub, once opened. |

### Syncing

`POST /repo/sync` compares against the commit synced last:

| The file | What happens |
|---|---|
| Changed only on GitHub | Written as a draft. |
| Changed both ways, and the lines merge | Merged (`sopops.merge`), and listed in `merged`. |
| Changed both ways, and they don't | Left alone and listed in `conflicts`. |

`error` is set when GitHub's files don't build with the drafts, and then nothing is written. Webhooks (`push`) and polling run the same sync, once a connection has been synced.

`POST /repo/send` syncs first. It answers `409` with nothing to send, with unresolved conflicts, or if the connection was never synced. When its pull request merges, the drafts are synced back, writing nothing new.

Repository events go to the sessions stream (`/v1/workspaces/{ws}/sessions/stream`) as `event: repo`, with `{topic: "repo", kind: synced | conflict | sync_error | sync_pr | pr_merged | resolved | broken, ...}`.

## The GitHub App

| Endpoint | What it does |
|---|---|
| `GET /v1/github/app` | `{configured, source: env \| db, app_id, slug, name, html_url, install_url, can_create, problem}`. Never the key or secret. |
| `GET /v1/workspaces/{ws}/github/install?return_to=` | `{url}`: GitHub's install page with a signed `state`. GitHub returns to `/v1/github/setup`, which lets the workspace use the installation and redirects to `return_to?github_installation=<id>`. |
| `GET /v1/workspaces/{ws}/github/installations` | `[{installation_id, account}]`: the installations this workspace may use. |
| `GET /v1/workspaces/{ws}/github/installations/{id}/repositories` | `[{owner, repo, full_name, default_branch, private}]`. |
| `POST /v1/github/app/manifest` | `{public_url, return_to, organization?, webhook_url?}` → `{action, manifest}`: the form to post to GitHub to create an App, with permissions and events filled in. GitHub returns to `/v1/github/app/manifest/callback`, which stores its id, key and webhook secret (encrypted). |
| `POST /v1/github/webhook` | GitHub's webhooks. |

```sh
curl -s "$SOPOPS_URL/v1/github/app"
```

```json
{
  "configured": true,
  "source": "db",
  "app_id": "1234567",
  "slug": "sopops-acme",
  "name": "sopops (acme)",
  "html_url": "https://github.com/apps/sopops-acme",
  "install_url": "https://github.com/apps/sopops-acme/installations/new",
  "can_create": false,
  "problem": null
}
```

Webhooks get `401` unless `X-Hub-Signature-256` matches the webhook secret, and each `X-GitHub-Delivery` is handled once.

| Event | What it does |
|---|---|
| `push` | Syncs. |
| `pull_request` closed | Updates suggestions, the onboarding import and the sync pull request at once. |
| `installation`, `installation_repositories` | Mark connections broken or working again. |

## Pull requests from code

Suggestions open pull requests in code.

```python
from sopops_server import github

url = github.open_pull_request(store, workspace_id, files, title, body, branch=None)
state = github.pr_status(store, workspace_id, url)  # "open", "merged" or "closed"
```

`open_pull_request` commits `files` (paths relative to the project's folder; `None` deletes) to a new branch `sopops/<id>`, or `branch`, off the base branch's head, as blobs, a tree, a commit and a ref.

It then opens a pull request against the base branch and returns its URL. It raises `NotFound` when the workspace has no repository, and `GitHubError` for anything GitHub refuses.
