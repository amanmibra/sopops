# 📋🗄️ Example: LiveKit restaurant agents, served by sopops

The same restaurants as sopc's [LiveKit restaurant example](https://github.com/amanmibra/sopc/tree/main/examples/livekit-restaurant): one phone agent per restaurant, sharing a brand voice, an allergen procedure and a closing.

| Restaurant | LiveKit `agent_name` | What's special |
|---|---|---|
| Tony's Pizza | `tonys-pizza` | pizza shop: pizza context, delivery |
| Luigi's Trattoria | `luigis-trattoria` | takes reservations and large orders |
| Sakura Sushi | `sakura-sushi` | reservations, **no delivery**, transfers to the head chef |

In sopc's example, each restaurant's prompt is built into `sops/build/` and ships with the code, so a wording change means redeploying every agent it reaches. Here the agent fetches its prompt from sopops when a call starts: a publish in sopops reaches the next call with no redeploy, a rollback is one click, and every call goes back to sopops with the release it ran on, to be checked against its procedures.

## What's in this folder

```
livekit-restaurant/
  agent.py          the LiveKit agent; one codebase, deployed once per restaurant
  tools.py          mock versions of the tools the SOPs name (lookup_allergens, transfer_to_staff, ...)
  pyproject.toml    livekit-agents, and sopops from ../../sdks/python
  .env.example      LiveKit and sopops settings
```

There's no `sops/` folder. The instructions are sopc's [`examples/livekit-restaurant/sops`](https://github.com/amanmibra/sopc/tree/main/examples/livekit-restaurant/sops), published into a sopops workspace once (below); after that they live in sopops, edited in its UI or kept in git and published by CI. Start with `agents/sakura-sushi.yaml` there to see how a restaurant's prompt is put together.

## How the agent uses it

The same code runs for every restaurant. Each deployment sets `AGENT_NAME`, which is both its LiveKit `agent_name` and its agent in sopops:

```python
# agent.py
session = AgentSession(stt=..., llm=..., tts=...)
ins = await attach(ctx, session, agent=AGENT_NAME, fallback=FALLBACK)
await session.start(room=ctx.room, agent=Agent(instructions=ins.text, tools=TOOLS))
```

`attach`, from the [Python client](../../sdks/python), does three things:

1. **Fetches the prompt** of the live release for `sakura-sushi`. The adapter scopes that identifier to LiveKit (`GET …/agents/sakura-sushi?platform=livekit`). The agent logs which one it got: `sopops: sakura-sushi release 4 (live)`.
2. **Notes the release** in `session.userdata` (`sopops_release`), so your own code can read it too.
3. **Posts the call to sopops when the job ends**: `session.history` with timestamps and tool calls, keyed by the job id, with the release. sopops checks it against each procedure that applied, and it shows on the Sessions page.

A sopops outage never blocks a call. The fetch gives up after 2 seconds and uses the last good copy for that agent on disk (`release 4 (cache)`), or, on a machine that has never fetched it, `FALLBACK`. A failed post at the end is logged, never raised into the job's shutdown.

The SOPs name tools (`lookup_allergens`, `check_reservations`, ...), so the agent registers tools with those exact names; `tools.py` has mock versions that return canned answers. `ins.tools` lists every tool the agent's prompt mentions. If the prompt names a tool the agent doesn't register, the model has nothing to call and may say the call out loud as text.

## Making a change

Say the team wants every agent to use the caller's name.

1. **Edit the shared text once**, in the sopops UI (Shared instructions → `brand-voice`) or in `sops/instructions/brand-voice.md` in git.
2. **See what it affects.** Unpublished changes lists the three agents it reaches, with the exact diff of each prompt.
3. **Publish.** The next call to each restaurant runs with the new prompt. Nothing is redeployed.
4. **Watch the sessions.** Each call records the release it ran on, so Insights → Release compare shows whether the change helped, procedure by procedure.

If it made things worse, make the previous release live again; the next call is back on it.

## Testing a release on some calls

To try a candidate on a share of calls, pin its release in the job's dispatch metadata (or the room's metadata). `attach` fetches that release instead of the live one:

```
lk dispatch create --new-room --agent-name sakura-sushi --metadata '{"sopops_release": 5}'
```

Each session records the release it ran on, so the two compare side by side.

## Streaming (optional)

With `SOPOPS_STREAM=1`, `agent.py` passes `stream=True`: each turn and tool call also goes to sopops as it happens, in batches from a background task, so a call shows up on the Sessions page while it's still going. Streaming never blocks the agent or raises into it; if sopops is slow or down, events are dropped and logged. The full call is still posted when the job ends and replaces what was streamed.

## Run it

Start sopops (the [Quickstart](../../README.md#quickstart)) and create a workspace named `prod`. Publish the restaurant instructions into it, from a clone of [sopc](https://github.com/amanmibra/sopc):

```sh
cd sopc/examples/livekit-restaurant
python3 -c 'import json,pathlib; r=pathlib.Path("sops"); print(json.dumps({"files": {p.relative_to(r).as_posix(): p.read_text() for p in r.rglob("*") if p.is_file()}}))' \
  | curl -fsS -X POST -H 'Content-Type: application/json' -d @- http://127.0.0.1:8484/v1/workspaces/prod/publish
```

In sopops, make a token with the `runtime` and `ingest` scopes (Settings → Tokens). Then, in this folder:

```sh
cp .env.example .env.local              # LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, SOPOPS_URL, SOPOPS_TOKEN
uv sync
AGENT_NAME=sakura-sushi uv run agent.py dev
```

`uv run agent.py console` talks to the agent from your terminal instead of a LiveKit room.

## Compared with sopc's example

| | sopc's example (prompt in the build) | This example (prompt from sopops) |
|---|---|---|
| Where the prompt comes from | `sops/build/`, copied into the image | sopops, when each call starts |
| Getting a change live | Deploy each changed agent | Publish; the next call runs it |
| Rolling back | Revert and redeploy | Make the previous release live |
| If the prompt source is down | Can't happen: it's on disk | The last good copy on disk, then the fallback |
| What a call ran on | The prompt hash in the logs | The release, on every session in sopops |
| Checking calls against the SOPs | Not included | Every call, per procedure, on the Sessions page |
