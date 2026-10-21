---
name: sopops-local-setup
description: Walk someone through running sopops on their own machine, one step at a time. Checks what's installed, clones the repo, writes .env, starts sopops and Postgres with Docker, then guides them through the setup screens, bringing in their agents and connecting their calls. Use when the user asks to try, install, run or self-host sopops locally, or pastes the "Try it yourself locally" prompt from sopops.dev.
---

# Set up sopops locally

You're the guide. The person is trying sopops for the first time and may not know Docker, voice platforms or sopops itself. Go one step at a time: say in a sentence or two what the step does and why, do it, check that it worked, then move on. Don't paste the whole plan at once.

Call what a voice agent is told its **instructions** (what a platform may call the prompt or system prompt). A **call** is one conversation with the agent.

## Rules

- **Keys and passwords never go through you.** The person types model keys, platform keys and passwords into the sopops page in their browser, or into `.env` in their editor. Never ask them to paste a key in the chat, never print `.env`, and never echo a secret you generated.
- sopops stays on `127.0.0.1`. Don't change the `ports` in `docker-compose.yml` to `0.0.0.0` or put it on the internet.
- Never run `docker compose down -v` or delete the `pgdata` volume: that's their data.
- Ask before installing anything on their machine.

## 1. Say what's about to happen

In two or three sentences: you'll start sopops and its database in Docker, open it in the browser, and set up a team. Then they can bring in their agents' instructions and send calls to it. It takes about ten minutes. Ask whether they already run voice agents (LiveKit, ElevenLabs, Vapi or something else) and where those agents' instructions live. Their answer decides step 6.

## 2. Check what's installed

```sh
git --version
docker --version && docker compose version
docker info >/dev/null 2>&1 && echo "docker is running"
```

- **No Docker:** suggest Docker Desktop (https://www.docker.com/products/docker-desktop/) or, on a Mac, OrbStack (`brew install --cask orbstack`). Wait until `docker info` works.
- **Docker installed but not running:** ask them to open Docker Desktop or OrbStack, then check again.

## 3. Get the code

If the current folder is already the sopops repo (it has `docker-compose.yml` and `skills/sopops-local-setup`), skip this. Otherwise:

```sh
git clone https://github.com/amanmibra/sopops && cd sopops
```

## 4. Write .env

```sh
cp -n .env.example .env
```

Set `SOPOPS_SECRET_KEY`, which encrypts the model and platform keys sopops stores. Generate it straight into the file, without printing it:

```sh
key=$(openssl rand -base64 32 | tr '+/' '-_') && sed -i.bak "s|^SOPOPS_SECRET_KEY=.*|SOPOPS_SECRET_KEY=$key|" .env && rm .env.bak && unset key
grep -c '^SOPOPS_SECRET_KEY=.\+' .env   # 1 means it's set
```

If port 8484 is taken (`lsof -iTCP:8484 -sTCP:LISTEN`), set `SOPOPS_HOST_PORT` in `.env` to a free port, such as 8494, and use that port below.

Leave the model keys empty. The person adds them in the browser in step 5, where sopops tries each key before saving it.

## 5. Start sopops

```sh
docker compose up --build -d
```

The first build takes a few minutes. Tell them that, then wait until it answers:

```sh
for i in $(seq 1 90); do curl -sf http://127.0.0.1:8484/healthz && break; sleep 2; done
```

If it never answers, read `docker compose logs app --tail 80`, explain the problem in plain words and fix it.

Then open http://127.0.0.1:8484 (`open` on a Mac, `xdg-open` on Linux) and stay with them through the setup screens. They fill in the screens; you explain each one when they get to it:

| Screen | What to tell them |
|---|---|
| Name, email and password, team name | The account is only on their machine. Any email works. |
| Anyone else on your team | Skip for now. People can join later with a link. |
| Connect your AI models | sopops uses two models to check calls: a fast one for most decisions and a stronger one to review what it flags (for example Claude Haiku and Claude Sonnet). They paste their own key here. They can skip it: calls are still stored, just not checked until a model is set. |
| Which platforms do you use | Pick what their agents run on. |
| How sopops builds an agent's instructions | Instructions are split into blocks: shared instructions several agents use, and procedures (the steps of a task, like taking an order). Change a block once and every agent that uses it changes. |
| Bring in your agents | Step 6 below. |
| Publish your first release | A release is a snapshot of every agent's instructions. Agents get the live one when a call starts. |
| Connect your calls | Step 7 below. |

## 6. Bring in their agents

- **Their agents' instructions are in a repo:** on the sopops **Import agents** page, choose **Claude Code or Codex** and copy the command it shows. It has a token that lasts two hours. Have them run it in a terminal **in their agents' repo**, not in this one. A second agent session then finds every agent's instructions, splits them into blocks and sends them back to sopops for review. That session follows the `sopops-onboard` skill. Afterwards, the person reviews the proposal on the page and applies it.
- **On ElevenLabs or Vapi with no repo:** the Import page can read agents straight from the platform with a read-only key, which they paste on the page.
- **No agents yet, or just looking:** paste an example prompt on the Import page (**Paste or upload**, which needs the model key from step 5), or show them the walkthrough (`examples/walkthrough/WALKTHROUGH.md`). It sends 300 sample calls with planted mistakes through sopops, without keys. It needs `just`, `uv`, `pnpm` and the sopc compiler, so ask before installing any of them.

## 7. Connect their calls

Every call has to come back to sopops to be checked. Send them to the right part of `docs/connect.md` for their platform:

- **LiveKit:** `attach()` from the Python SDK, set up in a few lines in their agent. Set `SOPOPS_URL=http://127.0.0.1:8484` and `SOPOPS_TOKEN` to a token from **Settings → Access**, which they make and paste themselves. Offer to make the change in their agent's code, in their repo, if they want.
- **ElevenLabs or Vapi:** these platforms send calls to sopops by webhook, so the platform has to reach their machine. Locally that needs a tunnel (for example `cloudflared tunnel --url http://127.0.0.1:8484`), with `SOPOPS_PUBLIC_URL` set to the tunnel's address. Explain that the tunnel opens sopops to the internet for as long as it runs, and that sign-in must stay on. Leave this for later if they'd rather not.
- **Anything else:** `POST /v1/workspaces/{ws}/sessions` (see `docs/api/sessions.md`).

After the first call comes in, show them where it is: **Sessions** has the call with each step marked followed or missed, and **Patterns** groups the same mistake across calls once there are a few.

## 8. Finish

Wrap up in a few lines: where sopops runs, what's set up, and what's left (a model key, connecting calls). Tell them how to stop and start it:

```sh
docker compose stop      # stop, keeps the data
docker compose up -d     # start again
git pull && docker compose up --build -d   # update
```

Their data stays in the Docker volume `pgdata` until they delete it. For a hosted workspace instead of their own machine, they can book a call at https://sopops.dev.
