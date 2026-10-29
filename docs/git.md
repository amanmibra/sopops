# Git or database

## Keep SOPs in the database

Edit everything in the UI or over the `/config` API. Nobody edits YAML or Markdown, and each block is still a sop file, versioned on its own. `GET /v1/workspaces/{ws}/files?content=true` exports the workspace as sop files to switch to Git.

## Publish from Git with CI

Keep the sop folder in your repo. CI publishes it on merge:

```sh
python3 -c 'import json,pathlib; r=pathlib.Path("sops"); print(json.dumps({"files": {p.relative_to(r).as_posix(): p.read_text() for p in r.rglob("*") if p.is_file()}}))' \
  | curl -fsS -X POST -H 'Content-Type: application/json' -d @- "$SOPOPS_URL/publish"
```

`build/` is ignored. Folders in the format of sop v0.0.8 or earlier (then named sopc), or that still set `locked` (v0.0.9), are refused with a message to run `sop migrate`. Workspaces stored that way show a Convert page.

## Connect a GitHub repository

sopops syncs a workspace with a sop folder in your repo, both ways. Connect in Settings (gear icon) or with `POST /v1/workspaces/{ws}/repo`, giving the repository, branch (default `main`) and folder (default `sops`). The folder holds `sop.yaml`. A folder that still has `sopc.yaml` (its name in sop v0.0.8 and earlier, then named sopc) and no `sop.yaml` is read as if it were `sop.yaml`, and the next Send drafts to GitHub renames it in the sync pull request.

### With the GitHub App (preferred)

Install it on GitHub, choose the repository, and pick it in sopops.

| Permission | Access | For |
|---|---|---|
| Contents | Read and write | Reading the project; committing to sopops's own branches |
| Pull requests | Read and write | Opening pull requests and tracking merges |
| Metadata | Read | Required by GitHub |

Events: `push` and `pull_request` (GitHub always sends `installation` and `installation_repositories`).

**Hosted.** Register one App under GitHub → Settings → Developer settings → GitHub Apps → New, with those permissions and events.

- Webhook URL `https://{host}/v1/github/webhook`, with a webhook secret.
- Setup URL `https://{host}/v1/github/setup`, with "Redirect on update".
- Generate a private key, then set `SOPOPS_GITHUB_APP_ID`, `SOPOPS_GITHUB_APP_PRIVATE_KEY` (or `SOPOPS_GITHUB_APP_PRIVATE_KEY_PATH`), `SOPOPS_GITHUB_WEBHOOK_SECRET` and `SOPOPS_GITHUB_APP_SLUG`.

**Self-hosted.** With `SOPOPS_SECRET_KEY` set and no App, Settings offers **Create a GitHub App**. GitHub's manifest flow sets it up, and sopops stores the key and webhook secret encrypted. GitHub can't reach `127.0.0.1`, so point the webhook at a smee.io channel. Without webhooks, sopops polls.

### With a fine-grained token

Use a personal access token for the one repository, with the same permissions, stored encrypted with `SOPOPS_SECRET_KEY` and never returned. sopops polls unless you add a repository webhook signed with `SOPOPS_GITHUB_WEBHOOK_SECRET`.

## Sync from GitHub

A push to the connected branch that touches the folder syncs it into drafts. Pushes arrive by webhook, by polling once a minute while Settings is open, and on the `sopops pr-status` cron. Automatic syncs start after the first one.

- Files changed only on GitHub are written.
- Files changed both ways merge line by line. Ones that can't merge show in Settings as conflicts: keep yours, take GitHub's, or edit.
- Files deleted on GitHub are deleted here only if unedited here since the last sync.
- Each sync is noted "Synced from GitHub abc1234".

**Sync now** (`POST /v1/workspaces/{ws}/repo/sync`) syncs on demand. **Pull** (`POST /v1/workspaces/{ws}/repo/pull`) makes the drafts exactly match the folder.

## Send to GitHub

sopops never pushes to the base branch.

- **Send drafts to GitHub** (`POST /v1/workspaces/{ws}/repo/send`) commits the drafts' changes to `sopops/sync` and opens one pull request; sending again updates it. After a merge, the drafts sync back with nothing new.
- **Suggestions** go to their own `sopops/<id>` branch and pull request.
- With the App, sopops also opens the onboarding pull request.

## Test webhooks locally

```sh
npx smee-client --url https://smee.io/<channel> --target http://127.0.0.1:8484/v1/github/webhook
```

Or, for a repository you admin (needs `gh extension install cli/gh-webhook`):

```sh
gh webhook forward --repo=OWNER/REPO --events=push,pull_request \
  --url=http://127.0.0.1:8484/v1/github/webhook --secret="$SOPOPS_GITHUB_WEBHOOK_SECRET"
```
