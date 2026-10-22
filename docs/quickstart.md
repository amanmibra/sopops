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

Open http://127.0.0.1:8484. Setup asks for your name and team, then the AI models sopops checks each call with. Every setting is in [Configuration](configuration.md).

## Bring in your agents

- **A repo:** the **Import agents** page gives you one command for Claude Code or Codex. Run it where your prompts live. The agent finds each one, splits it into shared instructions and procedures, and sends it back as drafts.
- **A paste or an upload** of the prompt text.
- **ElevenLabs or Vapi:** read the agents straight off the platform.

See [Import your agents](import.md).

## Connect your calls

Every call has to come back to sopops to be checked. On LiveKit that is a few lines of code; ElevenLabs and Vapi send calls by webhook, with none. Install the client (`pip install "sopops[livekit]"` or `npm install sopops`), then see [Connect your agent](connect.md).

## See it work first

The walkthrough sends 300 synthetic calls with planted mistakes through sopops, with fake models and no keys, in about a minute. See [the walkthrough](../examples/walkthrough/WALKTHROUGH.md).
