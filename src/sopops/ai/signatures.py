"""Every typed model call, as a DSPy signature: inputs and Pydantic outputs, with the instructions as the docstring.

- `SplitRule`: one procedure rule rewritten as literal yes/no checks (a condition and positive actions), which Jev
  answers one by one. Run once per rule version and stored (`sopops.ai.rule_checks`).
- `CheckRules`: the reasoning model's review of some of a procedure's rules (the check prompt, `prompts/check.md`,
  restricted to the rules listed), for the rules Jev flags or isn't sure about.
"""

from __future__ import annotations

from typing import Literal

import dspy
from pydantic import BaseModel, Field

from ..pipeline import prompts
from ..pipeline.rollup import JUDGED_STEP_STATUSES, NEVER_STATUSES, WARNING_STATUSES

RULE_KINDS = ("step", "never", "warning")
ALL_STATUSES = tuple(dict.fromkeys(JUDGED_STEP_STATUSES + NEVER_STATUSES + WARNING_STATUSES))


# --- split a rule into literal checks ----------------------------------------------------------------------------


class RuleChecks(BaseModel):
    condition: str | None = Field(
        None,
        description="The situation the rule is about, as a plain statement of what happens in the conversation. Steps: "
        "when the step applies (empty if it applies to every conversation). Warning signs: the situation to watch for, "
        "from the user's side. Never rules: always empty.",
    )
    actions: list[str] = Field(
        description="Positive statements of something the agent does that can be seen in the transcript, never 'does "
        "not'. Steps and warning signs: what the agent must do (every one must happen; empty for a warning sign that "
        "says nothing about what to do). Never rules: the forbidden behaviour itself, happening.",
    )
    forbidden: list[str] = Field(
        default_factory=list,
        description="Warning signs only: what the agent must not do once the situation comes up, stated as that "
        "behaviour happening ('The agent offers an add-on after ...'). Empty for steps and never rules.",
    )


class SplitRule(dspy.Signature):
    """Rewrite one rule of an AI agent's procedure (SOP) as literal yes/no checks about a conversation transcript.

    A classifier answers each check on its own, literally, from the transcript, so every check is one positive, atomic
    judgment: "did this happen", never "was this avoided".

    condition: for a step, when the step says or implies the situations it applies to ("if ...", "when ...", "only
    if ...", or a step about something only some conversations have), state that situation, e.g. "The user orders an
    item that has required choices." Leave it empty when the step applies to every conversation in which the procedure
    applies. For a warning sign, the situation it describes, from the user's side ("The user corrects an item in their
    order."). Never rules have no condition: their "when ..." belongs in the action.

    actions: most rules are ONE action. Split only when the rule names separate things that must all happen. A list
    of examples, details or alternatives stays inside one action. Branches ("if the user declines, move on; if they
    accept, confirm it") are never separate actions: keep them in one action that is true when whichever branch arose
    was handled. Every action must be something that can be seen in the transcript (what is said, asked or done); leave
    out what it can't show. Never rules: state the forbidden behaviour happening ("The agent reads back the whole order
    after a single item changed."), never its omission; don't add "fails to", "does not" or "without" that the rule
    doesn't say, and keep any "unless ..." exception in the action ("... when the user didn't ask for it").

    Keep the rule's own wording and meaning; placeholders like {{name}} stay as they are. Don't add requirements,
    examples or exceptions the rule doesn't state.
    """

    kind: Literal["step", "never", "warning"] = dspy.InputField(desc="step (the agent must do it), never (the agent must not), warning (a situation to watch for)")
    rule: str = dspy.InputField(desc="The rule as the procedure states it")
    procedure: str = dspy.InputField(desc="The procedure's name, goal and when it applies, for context only")
    checks: RuleChecks = dspy.OutputField()


# --- review some of a procedure's rules --------------------------------------------------------------------------


class Evidence(BaseModel):
    turn: int = Field(description="The turn number, as shown in the transcript.")
    quote: str = Field(description="Copied exactly from that turn's text; at most about 25 words.")


class RuleJudgment(BaseModel):
    rule: str = Field(description="The rule's key: step_N, never_N or warning_N.")
    reason: str = Field(description="One sentence, in English.")
    evidence: list[Evidence] = Field(description="Where it happened; may be empty for some statuses.")
    status: Literal[ALL_STATUSES] = Field(  # type: ignore[valid-type]
        description="Steps: done, skipped, not_reached or not_applicable. Never rules: violated or ok. Warning signs: present, unheeded or absent."
    )


class _CheckRules(dspy.Signature):
    transcript: str = dspy.InputField(desc="The conversation; turns are numbered")
    procedure: str = dspy.InputField(desc="The whole procedure, for context")
    rules_to_check: list[str] = dspy.InputField(desc="The keys of the rules to judge; judge only these")
    rules: list[RuleJudgment] = dspy.OutputField(desc="One entry per rule in rules_to_check, in that order")
    goal_reason: str = dspy.OutputField(desc="One sentence, in English")
    goal_reached: Literal["yes", "no", "unclear"] = dspy.OutputField()


RESTRICTED = (
    "\n\n## Which rules\n\nJudge only the rules listed in `rules_to_check`, one entry each; the rest of the procedure is "
    "context. Also judge the goal."
)
CheckRules = _CheckRules.with_instructions(prompts.load("check").text + RESTRICTED)
CHECK_RULES_VERSION = prompts.load("check").version + "+rules"
