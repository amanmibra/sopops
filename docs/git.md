# Git or database

**Database.** Everything lives in sopops's database and is edited in the UI or over the `/config` API. Nobody edits YAML or Markdown, but each block is still stored as a sopc file and versioned on its own.

**Git.** The sopc folder lives in your repo, and CI publishes it on merge in one call:

```sh
python3 -c 'import json,pathlib; r=pathlib.Path("sops"); print(json.dumps({"files": {p.relative_to(r).as_posix(): p.read_text() for p in r.rglob("*") if p.is_file()}}))' \
  | curl -fsS -X POST -H 'Content-Type: application/json' -d @- "$SOPOPS_URL/publish"
```

`build/` is ignored. A folder in the format of sopc v0.0.8 or earlier, or one that still sets `locked` (sopc v0.0.9), is refused with a message to run `sopc migrate`; a workspace stored that way shows a Convert page in the UI.

You can switch any time: `GET …/files?content=true` exports a workspace as sopc files to commit.

### Connect a GitHub repository

Instead of CI, sopops can keep the workspace and the repo's sopc folder in sync both ways. Connect it under the gear icon (Settings), or with `POST …/repo`: the repository, the branch (default `main`) and the sopc project's folder (default `sops`, where onboarding puts it). sopops signs in one of two ways.

**The GitHub App** (preferred). Install on GitHub, choose the repository there, and pick it in sopops. It needs these permissions, and the `push` and `pull_request` events (GitHub always sends `installation` and `installation_repositories`):

| Permission | Access | For |
|---|---|---|
| Contents | Read and write | Reading the project; committing to sopops's own branches |
| Pull requests | Read and write | Opening pull requests, and following whether they merge |
| Metadata | Read | Required by GitHub |

- *Hosted:* register one App (GitHub → Settings → Developer settings → GitHub Apps → New) with those permissions and events, the webhook URL `https://<host>/v1/github/webhook` and a webhook secret, and the setup URL `https://<host>/v1/github/setup` (with "Redirect on update"). Generate a private key and set `SOPOPS_GITHUB_APP_ID`, `SOPOPS_GITHUB_APP_PRIVATE_KEY` (or `SOPOPS_GITHUB_APP_PRIVATE_KEY_PATH`), `SOPOPS_GITHUB_WEBHOOK_SECRET` and `SOPOPS_GITHUB_APP_SLUG`.
- *Self-hosted:* with `SOPOPS_SECRET_KEY` set and no App, Settings offers **Create a GitHub App**: GitHub's App manifest flow creates it with the right permissions, events and URLs, and sopops stores its key and webhook secret encrypted. GitHub can't reach `127.0.0.1`, so give a smee.io channel as the webhook address to get webhooks locally; without webhooks, sopops polls instead.

**A fine-grained personal access token** for that one repository, with the same permissions, stored encrypted with `SOPOPS_SECRET_KEY` and never returned. Webhooks don't come with a token connection unless you add a repository webhook signed with `SOPOPS_GITHUB_WEBHOOK_SECRET`; sopops polls instead.

**GitHub to sopops.** A push to the branch that touches the folder (the webhook, or polling about once a minute while Settings is open and on the `sopops pr-status` cron) brings the folder into drafts: files changed only on GitHub are written, files changed both ways are merged line by line, and files that don't merge stay as they are here and show in Settings as conflicts to keep yours, take GitHub's, or edit. A file deleted on GitHub is deleted here only if it wasn't edited here since the last sync. Each sync is noted "Synced from GitHub abc1234". **Sync now** (`POST …/repo/sync`) does the same on demand; automatic syncs start after the first one. **Pull** (`POST …/repo/pull`) instead makes the drafts exactly the folder.

**sopops to GitHub.** **Send drafts to GitHub** (`POST …/repo/send`) puts the drafts' differences on the branch `sopops/sync` and opens one pull request, updated by sending again. When it merges, the drafts are synced back with nothing new to write. **Suggestions** are committed to their own `sopops/<id>` branch and opened as PRs. With the App, onboarding's pull request is opened by sopops too. sopops never pushes to the base branch.

**Testing webhooks locally.** Forward GitHub's deliveries to the local server with [smee](https://smee.io) (`npx smee-client --url https://smee.io/<channel> --target http://127.0.0.1:8484/v1/github/webhook`) or, for a repository you admin, `gh webhook forward --repo=OWNER/REPO --events=push,pull_request --url=http://127.0.0.1:8484/v1/github/webhook --secret="$SOPOPS_GITHUB_WEBHOOK_SECRET"` (needs `gh extension install cli/gh-webhook`).
