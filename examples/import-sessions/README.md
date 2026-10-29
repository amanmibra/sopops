# 📋🗄️ Example: import sessions from a file

Ten made-up restaurant calls and chats, one canonical session per line, for loading a workspace with sessions without running an agent. Use it to try the Sessions page, the SOP checks and Insights, or as a template for a backfill from your own call logs.

```
import-sessions/
  restaurant-sessions.jsonl     ten sessions, one JSON object per line
```

## What's in the file

| Restaurant | Sessions | What they show |
|---|---|---|
| Tony's Pizza | 4 | over LiveKit: a delivery order, a pickup with a peanut allergy, a late order; and a web chat (`platform: api`, `channel: text`). One sends `agent` as its platform ref, `livekit:tonys-pizza`. |
| Luigi's Trattoria | 2 | a table for 12 that needs a manager to confirm, and a gluten-free question |
| Sakura Sushi | 2 | ElevenLabs conversations: an order, and a booking that hung up early (`partial: true`, no `ended_at`) |
| La Casita | 1 | a Vapi call answered in Spanish, with a delivery-zone check |
| Pho House | 1 | an agent the workspace doesn't have, and no release |

Each line is a session in the [canonical format](../../docs/api/sessions.md#the-canonical-format): `platform` and `external_id` (together, the session's key), `agent`, the `release` it ran on, `started_at` and `ended_at`, then `turns` (`role`, `text`, `offset_s`) and `tool_calls` (`name`, `arguments`, `result`). One line, with the turns cut short:

```json
{"platform": "livekit", "external_id": "AJ_tp_8f2k1", "channel": "voice", "agent": "tonys-pizza", "release": 1,
 "started_at": "2026-10-15T23:04:10+00:00", "ended_at": "2026-10-15T23:04:40+00:00",
 "turns": [{"role": "agent", "text": "Thanks for calling Tony's Pizza! Pickup or delivery today?", "offset_s": 0.8},
           {"role": "user", "text": "Delivery please. A large pepperoni and a garlic knots.", "offset_s": 4.2}, ...],
 "tool_calls": [{"name": "check_delivery_zone", "arguments": {"address": "221 Mercer St"}, "result": {"in_zone": true, "eta_min": 35}, "offset_s": 15.1}]}
```

## Run it

Start sopops and make a workspace with the restaurant agents (the [Quickstart](../../README.md#quickstart), then publish the [restaurant SOPs](https://github.com/sopops-dev/sop/tree/main/examples/livekit-restaurant/sops) as described in [Git or database](../../README.md#git-or-database)). Then, from the repo root:

```
uv run sopops import examples/import-sessions/restaurant-sessions.jsonl --workspace prod
```

`sopops import` writes straight into the database named by `DATABASE_URL` (no token, no running server needed) and runs the SOP checks for each new or changed session. It prints what it did:

```
line 10: No agent 'pho-house' in release 1; the session keeps the name as sent.
10 created, 0 updated, 0 unchanged, 0 failed (pipeline: inline)
```

The Pho House warning is on purpose: a session for an agent the workspace doesn't have is kept with the name it was sent with. Warnings never stop an import. On a workspace with nothing published yet, each session warns `Release 1 doesn't exist in workspace 'prod'; the session is kept without it.` instead, and is still imported. A line that doesn't parse is reported by number and skipped, and the command exits 1.

Importing the same file again changes nothing (`0 created, 0 updated, 10 unchanged`): sessions are keyed by `platform` and `external_id`, so a backfill can be rerun safely. Change a line and import again, and only that session is updated and checked again.

Without `SOPOPS_MODEL_FAST` and `SOPOPS_MODEL_REASONING`, the sessions are stored but not checked.

## Your own sessions

Write one line per session in the same format and import it the same way. `-` reads stdin, so a converter can pipe straight in:

```
python convert_my_logs.py | uv run sopops import - --workspace prod
```

To send sessions from a running sopops instead of the database, `POST …/sessions` takes the same JSON with an `ingest` token (see [API reference](../../docs/api/sessions.md)).
