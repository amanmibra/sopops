# Quickstart

sopops runs on your own machine with Docker, or hosted at app.sopops.dev. This page sets it up locally.

## Set it up with Claude Code or Codex

Paste this in a terminal. Your coding agent clones sopops and walks you through setup one step at a time, with the [local setup skill](../skills/sopops-local-setup/SKILL.md).

```sh
claude "Set up github.com/amanmibra/sopops on my machine with me, using its sopops-local-setup skill"
```

With Codex, start the same prompt with `codex` instead of `claude`. You type every key and password into sopops in your browser; none of them go through the agent.

## Set it up by hand

You need git and Docker.

```sh
git clone https://github.com/amanmibra/sopops && cd sopops
cp .env.example .env        # optional: model keys, sign-in, email
docker compose up --build   # sopops and Postgres on http://127.0.0.1:8484
```

Open http://127.0.0.1:8484. Setup asks for your name and team, then your AI models: sopops uses them to check each call. Every setting is in [Configuration](configuration.md).

## Bring in your agents

If your agents' prompts live in a repo, the **Import agents** page gives you one command for Claude Code or Codex. Run it in that repo, and the agent finds each prompt, splits it into shared instructions and procedures, and sends it back to sopops for you to review. You can also paste a prompt in, or read agents from ElevenLabs or Vapi. See [Import your agents](import.md).

## Connect your calls

Install the client (`pip install "sopops[livekit]"`, or `npm install sopops`), then point your agent at your workspace. Each call has to come back to sopops to be checked. On LiveKit that's a few lines of code; ElevenLabs and Vapi send calls by webhook, with no code. See [Connect your agent](connect.md).

## See it work first

The walkthrough sends 300 synthetic calls with planted mistakes through sopops, with fake models and no keys, in about a minute. See [the walkthrough](../examples/walkthrough/WALKTHROUGH.md).
