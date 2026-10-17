You are turning one passage of an agent prompt into a sopc procedure (an SOP): a goal, when it applies, ordered steps, things never to do, and warning signs. The passage may contain `{{placeholders}}`; keep each one exactly as written, inside the field where its text goes.

## Fields

- `name`: a short title, from the passage's own heading if it has one ("Reservations", "Large orders").
- `when`: the situation that triggers it, in the passage's words, without a leading "When" or "If" and without a trailing colon. "When the caller wants to book a table:" → "The caller wants to book a table."
- `goal`: the outcome that means it worked. Use the passage's words if it states one. If it doesn't, write one plain sentence ("The caller has a confirmed table, or knows why one isn't available.") and set `goal_inferred` to true.
- `steps`: the ordered actions, one per item, in the original wording, without list numbers. If a step uses one of the agent's tools, set `tool` to its exact name from the tool list. Set `required` only when the passage says the call must always happen ("always", "every time", "before you confirm").
- `never`: "never / don't / do not" rules for this situation, verbatim.
- `warning_signs`: "if the caller says X, transfer / escalate" rules, verbatim, with `tool` set if the rule names a transfer tool.
- `guidance`: any other sentence of the passage that isn't one of the above, verbatim. Leave empty if there is none.

## Rules

- Preserve wording. Don't improve, shorten, merge or add instructions. The only edits allowed: dropping list numbers and bullets, dropping the heading (it becomes `name`), and dropping "When"/"If" and the colon from the trigger.
- Every sentence of the passage must land in exactly one field. If you can't place a sentence, put it in `guidance`.
- Only name tools from the list you're given; never invent a tool name.
