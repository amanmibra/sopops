# Extracting agent prompts into sopops

You are turning a team's hand-written voice agent prompts into a sopops proposal: shared text written once as **instructions**, step-by-step situations as **procedures**, and per agent its own **context** plus the ordered list of blocks it uses. Aim for reusable blocks without changing what any prompt says or does. The compiled prompts must say everything the originals said. You do the judgment; the sopops tools do the compiling and checking; the user decides.

## 1. Collect the originals (read-only)

- Find every agent's prompt where it lives: agent code (LiveKit `Agent(instructions=...)`, Pipecat, custom), prompt files, env or config, a database (use the project's existing tooling or a read-only query), or the platform (`list_platform_agents` / `get_agent_prompt` for ElevenLabs and Vapi).
- Resolve string concatenation and templating so each original is the exact text the model receives. Keep runtime placeholders (`${restaurantName}`, `{{customer_name}}`) as written.
- Note each agent's platform (`livekit`, `elevenlabs` or `vapi`), its id there (LiveKit `agent_name`, ElevenLabs `agent_id`, Vapi assistant id) and the tool names it can call.
- Never change, deploy or delete anything while reading. Never print or copy API keys.

## 2. Classify every passage

| Text in the originals | Goes to |
|---|---|
| Facts about one agent only (location, hours, owner, menu specifics, payment) | That agent's `context` (first in its prompt) |
| Text several agents share, or could share with a value swapped (identity, tone, policies, closing lines) | An **instruction** block |
| A situation and what to do in it ("When the caller wants to book a table: ...") | A **procedure** block |
| The same blocks in the same order in many agents | Optionally a **group** |
| A near-copy that differs only by a value (a name, hours, a link, a phone number) | One block with a `{{variable}}`; each agent's value in its `variables` |
| A near-copy that differs in a rule ("once" vs "twice", "before" vs "after") | Drift: keep each agent's behavior (a variable, or one block per version) and report it as a finding |
| Said only at the end of the call | An instruction each agent lists last |

Section headings that only introduce other sections ("# Procedures") are dropped: sopc adds its own heading (`settings.procedures_heading`) before the procedures.

## 3. Procedures

Each procedure has: `name` (short title, from its heading), `when` (the trigger, in the original words, no leading "When"/"If", no trailing colon), `goal` (the outcome that means it worked; the original's words, or one plain sentence you write, with `goal_inferred: true`), `steps` (ordered actions, original wording, no list numbers; `tool` set to the exact tool name if the step uses one; `required: true` only if the original says it must always happen), `never` ("never / don't" rules), `warning_signs` ("if the caller says X, transfer" rules), and `guidance` (any other sentence of the passage).

## 4. Rules while writing

- **Make reusable blocks, safely.** Give equivalent passages across agents the same topic and block. Replace only true per-agent values with `{{variables}}`. If a rule, condition, tool, limit, timing or exception differs, keep both versions and report it as a finding; never silently choose one.
- **Preserve wording.** Don't improve, shorten, merge or reorder instructions. Allowed edits: replacing a value with a `{{variable}}`, removing a sentence a shared block now provides, and the procedure field split above.
- **Don't drop anything.** A sentence you can't place goes into that agent's `context`.
- Each agent lists its blocks in the order the text appeared in its original. `context` always goes first in the compiled prompt; mention passages that moved.
- Ids are lowercase kebab-case (`brand-voice`, `allergen-check`); instructions, procedures and groups share one namespace. Variable names are snake_case.
- Blocks never say which agents use them; agents list blocks.

## 5. Check fidelity, then fix

Call `check_fidelity(proposal, originals)`. For each agent it compiles the prompt with sopc and compares it sentence by sentence with the original: **missing** and **changed** sentences are lost or altered instructions; fix them and check again until every agent is ok. **Reworded** lines (a sentence split into steps) are fine; **added** lines should only be procedure headings and goals you wrote.

## Proposal format

```json
{
  "agents": [{"id": "tonys-pizza", "name": "Tony's Pizza", "platform": "livekit", "platform_id": "tonys-pizza",
              "context": "Tony's is a wood-fired pizza shop in Brooklyn.", "blocks": ["core", "allergen-check", "closing"],
              "variables": {"restaurant_name": "Tony's Pizza"}, "tools": ["lookup_allergens"]}],
  "instructions": [{"id": "closing", "title": "Closing", "text": "Before hanging up, repeat the order total."}],
  "procedures": [{"id": "allergen-check", "name": "Allergen check", "goal": "...", "goal_inferred": true, "when": "...",
                  "guidance": "", "steps": [{"text": "...", "tool": "lookup_allergens", "required": true}],
                  "never": [{"text": "Never say an item is allergen-free"}], "warning_signs": [], "delivery": "prompt"}],
  "groups": [{"id": "core", "blocks": ["restaurant-host", "brand-voice"]}],
  "settings": {"variables": {}, "procedures_heading": "## Procedures"}
}
```

`originals` is a separate object: `{"<agent id>": "<the original prompt, verbatim>"}`. Findings you want the reviewer to see (drift, near-copies) go in `findings`: `[{"id": "drift-upsell", "kind": "drift", "title": "Upsell rule", "message": "3 agents say once, golden-wok says twice", "block": "brand-voice", "variable": "upsell_limit", "variants": [{"agents": ["a", "b"], "text": "Never upsell more than once per call."}]}]`.
