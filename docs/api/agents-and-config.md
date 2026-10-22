# Agents and config

Edit what agents are told: agents, shared instructions, procedures, groups and settings, the sopc files behind them, and sopc's checks on files you send.

## Edit with forms

- Each item is one sopc file, so git publishing and exports keep working. sopops writes the file, then reads it back with `sopc export` to check it holds exactly what was sent.
- Saves are drafts. Nothing reaches agents until a [release](releases.md#publish-and-roll-back).
- Ids can't be renamed yet. Instructions, procedures and groups share one set of names.
- A signed-in member can call these, and so can a token with the `config` scope ([Tokens](teams-and-access.md#tokens)), which is how a coding agent keeps a workspace's SOPs up to date from a repo. Neither can publish: that stays with a person.

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces/{ws}/config` | Every item at head: `{workspace, settings, agents, instructions, procedures, groups}`, each with its `version`. |
| `GET/POST /v1/workspaces/{ws}/config/agents` | List agents, or create one: `{id, platform, platform_id, context, blocks, variables, author?, note?}`. `201` on create, `409` if it exists. |
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

```sh
curl -s -X PUT -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/config/procedures/allergen-check" \
  -d '{
    "name": "Allergen check",
    "goal": "The caller knows whether their order suits their allergy, from the allergen list.",
    "when": "The caller mentions a food allergy or a dietary restriction.",
    "steps": [
      {"text": "Ask who the allergy is for and what it is"},
      {"text": "Name the specific allergen back to the caller"},
      {"text": "Check each item against {{allergen_link}}.", "tool": "lookup_allergens"}
    ],
    "never": [{"text": "Never say an item is \"allergen-free\" or \"safe\""}],
    "author": "Ada"
  }'
```

```json
{
  "item": {
    "version": 2,
    "updated_at": "2026-10-19T01:47:03.495983Z",
    "updated_by": "Ada",
    "file": "procedures/allergen-check.md",
    "used_by": [{"agent": "bayleaf-bistro", "via": null}],
    "name": "Allergen check",
    "goal": "The caller knows whether their order suits their allergy, from the allergen list.",
    "when": "The caller mentions a food allergy or a dietary restriction.",
    "guidance": "",
    "steps": [{"text": "Ask who the allergy is for and what it is", "tool": null, "required": false}],
    "never": [{"text": "Never say an item is \"allergen-free\" or \"safe\"", "tool": null, "required": false}],
    "warning_signs": [],
    "delivery": "prompt",
    "id": "allergen-check",
    "format": "markdown"
  },
  "changed": [{"path": "procedures/allergen-check.md", "version": 2, "deleted": false}]
}
```

### Procedure fields

| Field | Type | |
|---|---|---|
| `id`, `name` | string | The procedure's id and title. |
| `goal`, `when`, `guidance` | string | What it is for, when it applies, how to handle it. |
| `steps` | `[{text, tool?, required?}]` | In order, at least one. `tool` is the exact name of a tool the agent has; `required` means that call has to happen. |
| `never`, `warning_signs` | same shape | Rules the agent must not break, and conditions to watch for. |
| `delivery` | `prompt \| auto \| tool` | Whether it goes in the prompt or is fetched with `get_sop`. |
| `format` | `markdown \| yaml` | Read only. New procedures are Markdown. One that came from git keeps its format unless Markdown can't hold an edit, and then it becomes YAML. |

Instructions, procedures and groups in `GET /config` also have `used_by: [{agent, via}]`, where `via` is the group the agent lists it through, or null. That call answers `422` with `old_format` issues when the workspace is in the sopc v0.0.8 format, or `removed_field` issues when it still sets `locked` (sopc v0.0.9). `POST /v1/workspaces/{ws}/migrate` converts either ([Releases](releases.md#publish-and-roll-back)).

## Read and write files

| Endpoint | What it does |
|---|---|
| `GET /v1/workspaces` | Workspaces and their current release. |
| `GET /v1/workspaces/{ws}/files` | Head: every file with its version. `?content=true` includes content (export to git). |
| `POST /v1/workspaces/{ws}/files` | `{changes: {path: content \| null}, author?, note?}`: edit or delete files atomically. Rejected if the result doesn't compile. |
| `PUT /v1/workspaces/{ws}/files` | `{files, author?, note?}`: replace head. Files not listed are deleted. |
| `GET /v1/workspaces/{ws}/files/{path}?version=N` | A file at head or at a version. |
| `GET /v1/workspaces/{ws}/history/{path}` | Every version of a file, newest first, without content. |

Paths under `build/` in uploads are ignored. A path that is absolute, uses `\` or contains `..` is an `unsafe_path` error.

```sh
curl -s -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SOPOPS_TOKEN" \
  "$SOPOPS_URL/v1/workspaces/harbor-qa/files" \
  -d '{"changes": {"instructions/closing.md": "Thank the caller by name.\n"}, "author": "Ada"}'
```

```json
{
  "workspace": "harbor-qa",
  "changed": [{"path": "instructions/closing.md", "version": 3, "deleted": false}]
}
```

## Check without saving

Nothing is stored.

| Endpoint | What it does |
|---|---|
| `POST /v1/validate` | `{files}`: sopc's checks. Returns `{valid, issues}`. |
| `POST /v1/render` | `{files}`: each agent's full instructions. Returns `{agents, lock, warnings}`. |
| `POST /v1/lint` | `{files}`: duplicate or conflicting instructions. Returns `{findings}`. |
| `POST /v1/migrate` | `{files}`: a folder in an older sopc format (v0.0.8, or with `locked`), converted with `sopc migrate`. Returns `{migrated, plan, files}`. |

```sh
curl -s -X POST -H "Content-Type: application/json" "$SOPOPS_URL/v1/validate" \
  -d '{"files": {"sopc.yaml": "version: 1\n", "agents/a.yaml": "livekit: a\nblocks:\n  - nope\n"}}'
```

```json
{
  "valid": false,
  "issues": [
    {
      "code": "unknown_block",
      "message": "blocks lists 'nope', which is not an instruction, SOP or group",
      "path": "agents/a.yaml",
      "severity": "error",
      "kind": "agent",
      "id": "a",
      "field": "blocks",
      "index": null,
      "text": "Lists 'nope', which isn't a shared instruction, procedure or group."
    }
  ]
}
```
