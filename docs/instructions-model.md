# How agent instructions are built

Every agent's instructions in sopops are assembled from small pieces: shared
text and procedures that many agents reuse, plus a little text that belongs to
one agent. This page explains each piece as a concept, how they come together
into one prompt, and the exact data behind each one: the file it's stored in,
the fields the API and forms use, and what each attribute means.

## The pieces

```
Workspace
├── Settings                defaults for placeholders, the procedures heading
├── Blocks                  reusable; a block never says which agents use it
│   ├── Shared instructions prompt text that isn't a procedure
│   └── Procedures (SOPs)   a goal, steps, never rules, warning signs
├── Groups                  a named, ordered list of blocks
└── Agents                  one per voice agent: its platform, its own
                            text, and the blocks it uses, in order
```

- A **block** is reusable text. There are two kinds: **shared instructions**
  (identity, brand voice, policy, a call closing) and **procedures**, also
  called SOPs (how to handle one kind of situation, step by step).
- An **agent** is the only place blocks come together. Shared text is written
  once in a block and reaches every agent that lists it, so a fix to a block
  reaches all of them in the next release.
- A **group** names a set of blocks that several agents share, so each of
  those agents can list the group instead of every block in it.
- **Settings** hold workspace-wide defaults for <code v-pre>{{placeholders}}</code>.

Each piece is one file in a [sop](https://github.com/sopops-dev/sop) folder.
The forms in sopops and the [config API](api/agents-and-config.md) edit those
files for you, so a workspace can also live in git and stay in sync ([Instructions in git](git.md)).

Ids are the file names without the extension, in lowercase kebab-case
(`instructions/brand-voice.md` has id `brand-voice`). Shared instructions,
procedures and groups share one set of names, so an id is unique across all
three. Agents have their own set.

## How one agent's prompt is assembled

1. The agent's own **context** comes first.
2. Then its **blocks**, in the order listed. A group expands in place, in the
   group's order (groups can contain groups).
3. The **procedures heading** (`## Procedures` by default) goes once, right
   before the first procedure. Everything after it follows in list order, so an
   instruction listed after the procedures (like a closing) ends the prompt.
4. Parts are separated by a blank line. Every <code v-pre>{{name}}</code> is then filled from the
   agent's `variables`, then the workspace's. A placeholder with no value in
   either stays as written, with a warning, for your code to fill at call start
   through the [SDK](sdk.md). A value passed there wins over both.

For example, this agent:

```yaml
# agents/copper-wok-eastside.yaml
livekit: copper-wok-eastside
context: Copper Wok Eastside is a noodle and wok counter on Fifth and Main.
blocks:
  - host-role         # shared instruction
  - brand-voice       # shared instruction
  - allergen-check    # procedure
  - delivery-orders   # procedure
  - closing           # shared instruction
variables:
  restaurant_name: Copper Wok Eastside
```

compiles to its context, then `host-role` and `brand-voice`, then
`## Procedures`, the two procedures, and finally `closing`, with
<code v-pre>{{restaurant_name}}</code> filled in as "Copper Wok Eastside".

A block may appear only once in an agent's expanded list, whether it's listed
directly or through a group.

## Agents

One file per voice agent: which agent on which platform it is, the text only it
gets, and the blocks it uses.

**File:** `agents/<id>.yaml`

```yaml
livekit: copper-wok-eastside        # exactly one of livekit | vapi | elevenlabs | retell
context: |
  Copper Wok Eastside is a noodle and wok counter on Fifth and Main.
blocks:
  - host-role
  - delivery-orders
variables:
  restaurant_name: Copper Wok Eastside
```

**Fields** (forms and API):

| Field | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | required | The agent's sopops id, the file name. Can't be renamed yet. |
| `platform` | `livekit` \| `vapi` \| `elevenlabs` \| `retell` | required | The voice platform it runs on. In the file, the platform is the key: `livekit: ...`. |
| `platform_id` | string | required | The agent's id on that platform: LiveKit `agent_name`, Vapi assistant id, ElevenLabs or Retell `agent_id`. Two agents can't point at the same platform agent. Together they form the agent's **platform ref**, e.g. `livekit:copper-wok-eastside`. |
| `context` | string | `""` | Text only this agent gets. It goes first in its prompt. |
| `blocks` | list of ids | `[]` | Shared instructions, procedures and groups, in prompt order. |
| `variables` | map of string → string | `{}` | Values for <code v-pre>{{placeholders}}</code> in any block this agent uses. They override the workspace defaults. |

## Shared instructions

Prompt text that isn't a procedure: who the agent is, how it speaks, facts and
policy, how it ends a call. It says nothing about which agents use it.

**File:** `instructions/<id>.md`, Markdown. Optional YAML front matter between
`---` lines holds notes for people; it never reaches a prompt.

```markdown
You are the phone host for {{restaurant_name}}. You take orders and bookings,
answer questions about the menu and hours, and hand off to staff when needed.
```

**Fields:**

| Field | Type | Meaning |
|---|---|---|
| `id` | string | The file name. |
| `text` | string | The prompt text, exactly as it goes in, with <code v-pre>{{placeholders}}</code>. |

## Procedures (SOPs)

How the agent should handle one kind of situation: what success looks like,
when it applies, the steps in order, what never to do, and what to watch for.
Procedures are what sopops checks calls against: each step, never rule and
warning sign becomes a [rule](#from-procedures-to-rules).

**File:** `procedures/<id>.md` (recommended) or `procedures/<id>.yaml`. Both
hold the same fields and compile to the same prompt.

```markdown
---
delivery: prompt
---
# Allergen check

**Goal:** The caller knows whether their order suits their allergy, from the allergen list.
**When:** The caller mentions a food allergy or a dietary restriction.

Parents often ask on behalf of a child. Confirm who the allergy is for first.

## Steps
1. Ask who the allergy is for and what it is
2. Check each item against {{menu_allergen_link}} `tool: lookup_allergens` `required`

## Never
- Never say an item is "allergen-free" or "safe"

## Warning signs
- The caller mentions anaphylaxis or an EpiPen; transfer to {{staff_transfer}} `tool: transfer_to_staff`
```

**Fields** (forms and API; the Markdown and YAML spellings are in the last column):

| Field | Type | Default | Meaning | In the file |
|---|---|---|---|---|
| `id` | string | required | The file name. | file name |
| `name` | string | required | The procedure's title, and its heading in the prompt. | `# Name` / `name` |
| `goal` | string | `""` | The outcome that means the procedure succeeded. sopops uses it to judge whether a call got there another way. Leaving it out is a warning. | `**Goal:**` / `description` |
| `when` | string | `""` | When the procedure applies. sopops uses it to decide which procedures a call is checked against. | `**When:**` / `scope` |
| `guidance` | string | `""` | Any other advice that isn't a step, never rule or warning sign. | paragraphs after the name / `guidance` |
| `steps` | list of [rule items](#rule-items) | required, at least one | What the agent does, in order. | `## Steps` (numbered) / `procedureSteps` |
| `never` | list of rule items | `[]` | Things the agent must never do. | `## Never` / `forbiddenActions` |
| `warning_signs` | list of rule items | `[]` | Situations that need special handling, usually a transfer. | `## Warning signs` / `warningSigns` |
| `delivery` | `prompt` \| `auto` \| `tool` | `prompt` | Where the content goes; see below. | front matter / `delivery` |
| `format` | `markdown` \| `yaml` | — | Read only: which file format it's stored in. New procedures are Markdown. | file extension |

### Rule items

Every entry in `steps`, `never` and `warning_signs` has the same shape:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `text` | string | required | What the agent does, must never do, or should watch for. May contain <code v-pre>{{placeholders}}</code>. Two items of one kind in a procedure can't have the same text. |
| `tool` | string \| null | `null` | The exact name of a tool the agent has, as its code or platform defines it. The prompt then tells the agent to use it. |
| `required` | bool | `false` | Steps only: the tool call has to happen. When a call's tool calls are sent, sopops checks a required step against them, not just the transcript: if the tool was never called, the step is skipped, whatever the agent said. |

In Markdown, `tool` and `required` are markers in backticks at the end of a
line: `` `tool: lookup_allergens` `` and `` `required` ``.

### Delivery

| `delivery` | In the prompt | Fetched with the agent's `get_sop` tool |
|---|---|---|
| `prompt` | everything | nothing |
| `auto` | name, when, never rules, warning signs | goal, guidance, steps |
| `tool` | name, when | everything else |

Use `prompt` unless the agent has a `get_sop` tool. Never rules and warning
signs stay in the prompt under `auto` so they apply even if the agent never
fetches the procedure.

## Groups

A named, ordered list of blocks that several agents share. An agent lists the
group in its `blocks`, and it expands in place.

**File:** the `groups` map in `sop.yaml`.

```yaml
groups:
  delivery-orders:
    - allergen-check
    - delivery-handling
```

**Fields:**

| Field | Type | Meaning |
|---|---|---|
| `id` | string | The group's name. Shares one set of names with instructions and procedures. |
| `blocks` | list of ids | Shared instructions, procedures and other groups, in order. A group can't contain itself, directly or through another group. |

Deleting a group puts its blocks in its place wherever it was listed, so no
agent's prompt changes.

## Settings

Workspace-wide defaults.

**File:** `sop.yaml`, which also marks the root of the sop folder.

```yaml
version: 1
variables:
  staff_transfer: the manager on duty
sops_heading: "## Procedures"
```

| Field | Type | Default | Meaning | In the file |
|---|---|---|---|---|
| `variables` | map of string → string | `{}` | Default values for <code v-pre>{{placeholders}}</code>, for every agent. An agent's own `variables` win. | `variables` |
| `procedures_heading` | string | `"## Procedures"` | Placed once, before the first procedure in each prompt. `""` for none. | `sops_heading` |

## Placeholders

<code v-pre>{{name}}</code> works in any text: shared instructions, agent context, and every
procedure field. Names use letters, digits, `_`, `-` and `.`. Values come from
three places, and any of them can set any placeholder: a value your code passes
at call start (the [SDK](sdk.md#variables-from-sopops-or-your-code)), then the
agent's `variables`, then the workspace's. The first one set wins. sopops fills
its own values when the release is built and serves the unfilled template too,
so a value passed at call start can still replace one set here. Rules keep their placeholders, since one rule is shared by
every agent that uses the procedure; sopops fills in each agent's values
wherever a check or a page shows the rule.

## What sopops adds to each item

Reading an item from sopops (`GET /v1/workspaces/{ws}/config`, or one item)
returns its fields plus:

| Field | Meaning |
|---|---|
| `version` | The item's file version, counting every saved change. |
| `updated_at`, `updated_by` | When and by whom the current version was saved. |
| `file` | The sop file it lives in, e.g. `procedures/allergen-check.md`. |
| `used_by` | Blocks and groups only: `[{agent, via}]`, each agent that gets it, and `via`, the group it comes through (null if listed directly). |

Saving an item creates a **draft**. Nothing reaches an agent until someone
publishes a release. Every version is kept, so any item can be read or restored
at an earlier version.

## From procedures to rules

Each step, never rule and warning sign is a **rule**: the unit sopops checks
on every call and tracks over time.

| Field | Meaning |
|---|---|
| `rule_id` | A 12-character id from the procedure id, the rule's kind and its text (ignoring whitespace). The same rule keeps the same id in every release. Rewording a rule gives it a new id; a reworded rule from a suggested fix is linked to the old one, so its history carries over. |
| `kind` | `step`, `never` or `warning`. |
| `index` | Its position within its kind, from 1: step 1, never 2. |
| `text`, `tool`, `required` | As written in the procedure. |

On each call, every rule of every procedure that applied gets a status:

| Kind | Statuses |
|---|---|
| Step | `done`, `out_of_order`, `skipped`, `not_reached` (the call ended first), `not_applicable` |
| Never | `ok`, `violated` |
| Warning sign | `absent`, `present` (handled), `unheeded` (it came up and the agent didn't do what it says) |

`skipped`, `violated`, `out_of_order` and `unheeded` are **findings**, the
agent mistakes that show up on Patterns. [Checks and review](api/checks-and-review.md)
has the full result format.

## Releases

Publishing freezes the whole workspace into a numbered **release**. For each
agent, it records exactly what the agent receives:

| Field | Meaning |
|---|---|
| `release` | The release number. |
| `agent`, `platform_ref` | Which agent, e.g. `copper-wok-eastside`, and its platform id, stored with the platform in front as `<platform>:<id>` (`livekit:copper-wok-eastside`). |
| `prompt` | The full compiled instructions, placeholders filled with the values set in sopops. |
| `template` | `prompt` before its placeholders were filled, for code that fills them with its own values first. |
| `variables` | The values set in sopops (the agent's, then the workspace's) that filled `prompt`. |
| `hash` | A hash of the prompt, to tell versions apart at a glance. |
| `tools` | Every tool its procedures name. |
| `tool_sops` | Procedures it fetches with `get_sop` (delivery `auto` or `tool`). |
| `components` | Every file the prompt was built from, in order, at its version: `[{kind, id, path, version}]`. |

A call records the release it ran, so every check is tied to the exact text
the agent had. That's what lets sopops compare releases and show whether a fix
helped ([Releases](api/releases.md)).

The complete file format, every validation error and the JSON Schemas are in
sop's [FORMAT.md](https://github.com/sopops-dev/sop/blob/main/FORMAT.md).
