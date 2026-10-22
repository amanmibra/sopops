# Agents and config

These endpoints edit what agents are told: agents, shared instructions, procedures, groups and settings. They also read and write the sopc files behind them, and run sopc's checks without storing anything.

## Edit with forms

These are what the UI edits. Each item is stored as a sopc file, so git publishing and exports keep working. sopops writes the file, then reads it back with `sopc export` to check it holds exactly what was sent.

Saves are drafts: nothing reaches agents until a [release](releases.md). Ids can't be renamed yet. Instructions, procedures and groups share one set of names.

A signed-in member can call these, and so can a token with the `config` scope ([Tokens](teams-and-access.md#tokens)), which is how a coding agent keeps a workspace's SOPs up to date from a repo. Neither can publish: that stays with a person.

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/config` | Every item at head: `{settings, agents, instructions, procedures, groups}`, each with its `version`. See below. |
| `GET/POST /v1/workspaces/{ws}/config/agents` | List agents, or create one: `{id, platform, platform_id, context, blocks, variables, author?, note?}`. `409` if it exists. |
| `GET/PUT/DELETE /v1/workspaces/{ws}/config/agents/{id}` | Get one agent, create or replace it, or delete it. |
| `POST /v1/workspaces/{ws}/config/agents/{id}/blocks` | `{block, author?, note?}`: add a shared instruction, procedure or group at the end of an agent's blocks. `409` if the agent already lists it or gets it through a group. |
| `DELETE /v1/workspaces/{ws}/config/agents/{id}/blocks/{block}` | Take a block out of an agent's blocks. `409` if the agent gets it through a group. |
| `POST /v1/workspaces/{ws}/config/agents/{id}/preview` | An agent's fields, unsaved, compiled with the rest of head. Returns `{valid, issues, prompt, hash, blocks, release, released_prompt}` and saves nothing. |
| `/v1/workspaces/{ws}/config/instructions[/{id}]` | Shared instructions: `{id, text}`. Deleting one takes it out of every agent and group that lists it. |
| `/v1/workspaces/{ws}/config/procedures[/{id}]` | Procedures (SOPs). Fields below. Deleting one takes it out of every agent and group. |
| `/v1/workspaces/{ws}/config/groups[/{id}]` | Groups, kept in `sopc.yaml`: `{id, blocks}`. Deleting one lists its blocks in its place wherever it was used, so no agent's instructions change. |
| `GET/PUT /v1/workspaces/{ws}/config/settings` | `{variables, procedures_heading}`. |
| `GET /v1/workspaces/{ws}/config/{kind}/{id}/history` | Every version, newest first. Groups and settings list only the versions of `sopc.yaml` that changed them. |
| `GET /v1/workspaces/{ws}/config/{kind}/{id}/versions/{n}` | An older version, as fields. One that set `locked` is read the way `sopc migrate` converts it. One saved in the v0.0.8 format comes back with `legacy: true` and its `content`. |

**`GET /config` details.** Instructions, procedures and groups have `used_by: [{agent, via}]`, where `via` is the group the agent lists it through, or null. The call answers `422` with `old_format` issues if the workspace is in the sopc v0.0.8 format, or `removed_field` issues if it still sets `locked` (sopc v0.0.9). `POST /v1/workspaces/{ws}/migrate` converts either ([Releases](releases.md#publish-and-roll-back)).

**Procedure fields.** `{id, name, goal, when, guidance, steps: [{text, tool, required}], never, warning_signs, delivery: prompt|auto|tool}`. New procedures are written as Markdown. One that came from git keeps its format unless Markdown can't hold an edit; then it becomes YAML.

## Read and write files

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces` | Workspaces and their current release. |
| `GET /v1/workspaces/{ws}/files` | Head: every file with its version. `?content=true` includes content (export to git). |
| `POST /v1/workspaces/{ws}/files` | `{changes: {path: content \| null}, author?, note?}`: edit or delete files atomically. Rejected if the result doesn't compile. |
| `PUT /v1/workspaces/{ws}/files` | `{files, author?, note?}`: replace head. Files not listed are deleted. |
| `GET /v1/workspaces/{ws}/files/{path}?version=N` | A file at head or at a version. |
| `GET /v1/workspaces/{ws}/history/{path}` | Every version of a file. |

Paths under `build/` in uploads are ignored. Absolute paths and `..` are rejected.

## Check without saving

Nothing is stored.

| Endpoint | What it does |
|---|---|
| `POST /v1/validate`, `/v1/render`, `/v1/lint` | `{files}`: sopc's checks, each agent's full instructions, and duplicate or conflicting instructions. |
| `POST /v1/migrate` | `{files}`: a folder in an older sopc format (v0.0.8, or with `locked`), converted with `sopc migrate`. Returns `{migrated, plan, files}`. |
