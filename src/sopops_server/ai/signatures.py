"""Every model call, as a DSPy signature: typed inputs, Pydantic or decision outputs, the instructions as the docstring.

Two kinds, by output type:

- **Decision signatures** have only `Noul` / `Choice` outputs, so they run on TypeSafe's Jev when it's on and on the
  fast model otherwise (DSPy asks a generative model for probability evidence and decodes it the same way). Their
  outputs depend on the procedure or the findings at hand, so they're built per call: `route_signature`,
  `check_signature`, `evidence_signature`, `assign_signature` (and `KeyCheck`, which only proves a key works). Each output's question goes in the predictor's `fields`
  (`instructions` and `criteria`), which is also where calibrated thresholds live.
- **Generative signatures** run on the reasoning model (or the fast one, as noted): `SplitRule`, `CheckRules`,
  `LocateEvidence` (fast), `NameRulePattern`, `ClusterFindings`, `GroupThemes`, `SuggestEdit`, `SegmentPrompt`, `StructureProcedure`,
  `NameVariables` (fast).

`VERSIONS` names the instructions each stored result was made with; bump one whenever its text changes.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import dspy
from dspy.experimental import Choice, Noul
from pydantic import BaseModel, Field

from ..onboarding.types import ProcedureDraft, Segment
from ..pipeline.rollup import JUDGED_STEP_STATUSES, NEVER_STATUSES, WARNING_STATUSES

VERSIONS = {
    "route": "route-4",
    "decide": "decide-1",
    "check": "check-5",
    "split": "split-1",
    "locate": "locate-1",
    "assign": "assign-2",
    "cluster": "cluster-2",
    "name": "name-1",
    "themes": "themes-2",
    "suggest": "suggest-2",
    "segment": "segment-3",
    "procedure": "procedure-3",
    "variables": "variables-2",
}
ALL_STATUSES = tuple(dict.fromkeys(JUDGED_STEP_STATUSES + NEVER_STATUSES + WARNING_STATUSES))
AGENT = 'the agent (the AI agent handling the conversation; its utterances have speaker "agent")'


def field_name(text: str) -> str:
    """A Python identifier made from an id, for a signature field: `restaurant-takeout` -> `restaurant_takeout`."""
    name = re.sub(r"[^0-9a-zA-Z_]+", "_", text).strip("_").lower() or "x"
    return name if name[0].isalpha() else f"x_{name}"


class Utterance(BaseModel):
    id: str = Field(description="U followed by the turn number.")
    speaker: Literal["agent", "user", "tool", "system"]
    text: str


class ProcedureBrief(BaseModel):
    name: str
    goal: str = ""
    when: str = Field("", description="When the procedure applies.")


def decision(instructions: str | dict[str, Any], true: str | None = None, false: str | None = None) -> dict[str, Any]:
    """A decision output's question, as the predictor's `fields` entry: its instructions, and for a Noul its criteria."""
    out: dict[str, Any] = {"instructions": instructions}
    if true is not None or false is not None:
        out["criteria"] = {"true": true, "false": false}
    return out


def decision_predict(signature: type[dspy.Signature], questions: dict[str, dict[str, Any]], params: dict[str, dict[str, Any]] | None = None) -> dspy.Predict:
    """A Predict whose decision outputs ask `questions`, with calibrated `params` (e.g. thresholds) where given."""
    p = dspy.Predict(signature)
    for name, q in questions.items():
        p.fields[name] = {**q, **(params or {}).get(name, {})}
    return p


# --- route -------------------------------------------------------------------------------------------------------

ROUTE_INSTRUCTIONS = """You review conversations between an AI agent (a voice call or a text chat) and a user, to decide which procedures (SOPs) applied, so they can be checked.

For each procedure: did the user ask for, or start, what it covers (its `when` and `goal`)? It counts even if the user only got partway, changed their mind, or what they wanted turned out to be unavailable. It doesn't count when the conversation was about something else, a wrong number, a robocall, silence or small talk. And did the agent act on that request at least once (confirm it, ask about it, answer it, or start doing it)? How the conversation ended doesn't change either answer.

Transcripts of voice calls come from speech recognition and contain mistakes; read through them. Conversations can be in any language."""


def route_signature(keys: list[str]) -> type[dspy.Signature]:
    fields: dict[str, Any] = {
        "procedures": (dict[str, ProcedureBrief], dspy.InputField(desc="The procedures, by key")),
        "transcript": (list[Utterance], dspy.InputField(desc="The conversation, in order")),
    }
    for k in keys:
        fields[f"{k}_situation"] = (Noul, dspy.OutputField(desc=f"Did the user ask for what procedure {k} covers?"))
        fields[f"{k}_responded"] = (Noul, dspy.OutputField(desc=f"Did the agent act on the user's request for procedure {k}?"))
    return dspy.make_signature(fields, ROUTE_INSTRUCTIONS, "RouteSession")


def route_questions(keys: list[str]) -> dict[str, dict[str, Any]]:
    out = {}
    for k in keys:
        out[f"{k}_situation"] = decision(
            {"question": f"In `inputs.transcript`, did the user ask for, or start, what the procedure `inputs.procedures.{k}` covers?",
             "procedure": f"`inputs.procedures.{k}.when` says when it applies; `inputs.procedures.{k}.goal` is what it achieves."},
            true="At least one utterance shows the user asking for or starting what the procedure covers, even if they only got partway, changed their mind, or it turned out to be unavailable.",
            false="The conversation is about something else, or ends before the user says what they want.",
        )
        out[f"{k}_responded"] = decision(
            {"question": f"In `inputs.transcript`, did the agent act on the user's request for what `inputs.procedures.{k}` covers: confirm it, ask a question about it, answer it, or start doing it?"},
            true="At least one agent utterance does this.",
            false="No agent utterance does this.",
        )
    return out


# --- rules as literal checks ---------------------------------------------------------------------------------------


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


# --- check: decisions per rule, then evidence -----------------------------------------------------------------------

CHECK_INSTRUCTIONS = """You check one conversation between an AI agent (a voice call or a text chat) and a user against one procedure (SOP) the agent was told to follow. Each question asks whether one thing happened in the conversation; answer it literally, from the transcript. Voice transcripts come from speech recognition and contain mistakes; a garbled word or a repeated, cut-off sentence isn't a separate action. Conversations can be in any language."""


def check_signature(names: list[str]) -> type[dspy.Signature]:
    fields: dict[str, Any] = {
        "procedure": (ProcedureBrief, dspy.InputField(desc="The procedure the agent follows")),
        "transcript": (list[Utterance], dspy.InputField(desc="The conversation, in order")),
    }
    for n in names:
        fields[n] = (Noul, dspy.OutputField(desc=f"Check {n}: see its question."))
    return dspy.make_signature(fields, CHECK_INSTRUCTIONS, "CheckRuleDecisions")


def action_question(action: str, actor: str = AGENT) -> dict[str, Any]:
    who = actor.split(" (")[0]
    return decision(
        {"question": f"In `inputs.transcript`, did {actor} do what `action` describes?", "action": action},
        true=f"At least one utterance shows {who} doing this.",
        false=f"No utterance shows {who} doing this.",
    )


def condition_question(situation: str) -> dict[str, Any]:
    return decision(
        {"question": "In `inputs.transcript`, did the situation in `situation` arise?", "situation": situation},
        true="At least one utterance shows this situation arising.",
        false="No utterance shows this situation.",
    )


def goal_question() -> dict[str, Any]:
    return decision(
        {"question": "In `inputs.transcript`, by the end of the conversation, was the outcome in `inputs.procedure.goal` achieved?", "agent": AGENT},
        true="The utterances show the outcome reached, for example confirmed by the agent or the user.",
        false="The conversation ends without the utterances showing the outcome reached, or they show it abandoned or refused.",
    )


def evidence_signature(names: list[str], ids: list[str]) -> type[dspy.Signature]:
    """One Choice per action over utterance ids (System One only: a generative model gets `LocateEvidence`)."""
    option = Choice[tuple((i, None) for i in ids)]
    fields: dict[str, Any] = {
        "procedure": (ProcedureBrief, dspy.InputField(desc="The procedure the agent follows")),
        "transcript": (list[Utterance], dspy.InputField(desc="The conversation, in order")),
    }
    for n in names:
        fields[n] = (option, dspy.OutputField(desc=f"Where {n} happened: see its question."))
    return dspy.make_signature(fields, CHECK_INSTRUCTIONS, "FindEvidence")


def evidence_question(action: str, actor: str = AGENT) -> dict[str, Any]:
    return {"instructions": {"question": f"Which utterance in `inputs.transcript` is where {actor.split(' (')[0]} does what `action` describes?", "action": action}}


class Located(BaseModel):
    key: str
    turn: int | None = Field(description="The turn number where it happens first; null if it doesn't happen.")
    quote: str = Field("", description="Copied exactly from that turn's text; at most about 25 words.")


class LocateEvidence(dspy.Signature):
    """For each action, find the turn of the conversation where it happens first, and quote it exactly. Turns are
    numbered; `AGENT` is the agent, `USER` the other person. If an action doesn't happen, give a null turn."""

    transcript: str = dspy.InputField(desc="The conversation, one numbered turn per line")
    actions: dict[str, str] = dspy.InputField(desc="What to find, by key")
    located: list[Located] = dspy.OutputField(desc="One entry per action key")


# --- check: the reasoning model's review -------------------------------------------------------------------------------


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


class CheckRules(dspy.Signature):
    """You are a QA reviewer. You check one conversation between an AI agent (a voice call or a text chat) and a user against one procedure (SOP) the agent was told to follow, rule by rule. Judge only the rules listed in `rules_to_check`, one entry each; the rest of the procedure is context. Also judge the goal.

    The procedure has a goal, when it applies, optional guidance, numbered steps, "never" rules and warning signs. Judge every rule from what actually happened in the transcript (and the tool call log, if one is given). Turns are numbered: `AGENT` is the agent, `USER` the other person, `TOOL` a tool the agent called.

    ## Steps

    Give each step one status:
    - `done`: the agent did it, at any point, the way the step says. Judge by substance, not wording: a step is done if its purpose was achieved in the agent's own words. If the user already gave the information unprompted and the agent clearly took it into account, the step is done. Order doesn't matter here; it's worked out separately from the evidence turns.
    - `skipped`: the conversation went past the point where this step belonged, and the agent didn't do it, or did it in a way that clearly misses one of the step's explicit parts. That covers a required piece left out (a step that says to state the total, and the total was never stated) and an explicit limit clearly broken (several sentences where the step says one short sentence, several questions at once where it says one at a time); the reason says which part. Different wording, politeness, or a small extra detail don't make a step skipped.
    - `not_reached`: the conversation ended (the user hung up, the line dropped, the agent transferred the call, the transcript stops) before the point where this step would come. Use this for steps after the point where the conversation stopped.
    - `not_applicable`: the step's condition never arose (a step for "if the customer asks for X" when they didn't), or the step doesn't apply to this conversation's situation. When a step starts with a condition ("If ...", "When ..."), first decide whether the condition held. If it didn't, the step is `not_applicable`, even if the agent did the step's action anyway: doing something the procedure says not to do is judged under the never rules, not here.

    ## Never rules

    - `violated`: the agent did what the rule forbids. Only the agent's own words or actions count, never the user's.
    - `ok`: it didn't.

    Judge a never rule against its literal scope, exceptions included. The rule's own words decide what counts: if the rule forbids something "unless the user asks", then anything the user asked for, or that answers the user's question, is not a violation; if the rule names an exception, the exception is not a violation. Count an action only when it is clearly the kind the rule names. A detail said in passing while doing something the procedure tells the agent to do (for example, while confirming what the user asked for) is part of that action, not a separate forbidden one; so is the name of what the user asked for, including a size, count or variant that is part of how it's offered. Don't stretch a rule to cover behavior it doesn't name. `violated` needs a quote of the agent's own words that, on its own, shows the forbidden action; if you can't quote such words, the status is `ok`.

    ## Warning signs

    A warning sign describes a situation to watch for, often followed by what the agent should do when it happens.
    - `present`: the situation came up, and the agent handled it the way the warning sign says (or the sign says nothing about what to do).
    - `unheeded`: the situation came up, the warning sign says what the agent should do about it, and the agent didn't.
    - `absent`: the situation didn't come up.

    Decide whether the situation came up from the user's side of the conversation first: quote the user's words that show it (for example, the turns where the user corrected something, sounded unsure, or gave a vague answer). For `unheeded`, also quote the agent's turn that shows it didn't do what the sign says. A situation that showed up once in passing and was resolved in the next turn is usually `present` at most, not `unheeded`.

    ## Goal

    `goal_reached`: `yes` if the procedure's goal was achieved by the end of the conversation, `no` if it wasn't, `unclear` if the transcript can't tell.

    ## Evidence and reasons

    - For every status, give a one-sentence reason in English, whatever the conversation's language.
    - Evidence is a list of `{turn, quote}`: the turn number as shown, and a short quote (at most about 25 words) copied exactly from that turn's text, in the conversation's language. Give evidence for `done`, `violated`, `present` and `unheeded`: the turn(s) where it happened, first occurrence first. For `skipped`, cite the turn where the agent moved on without the step (or did it the wrong way), if there is one. `not_reached`, `not_applicable`, `ok` and `absent` can have no evidence.

    ## Be precise

    - Mark `skipped`, `violated` or `unheeded` only when the transcript clearly shows it. A reviewer will read each one; a wrong flag costs more than a missed one.
    - When the agent's words can reasonably be read as within what a rule allows, give the agent the benefit of the doubt: `ok` for a never rule, `done` for a step. Flag only what a reviewer reading your quote would agree breaks the rule as written.
    - A step is about what the agent does, not whether the outcome was possible: if the agent couldn't do a step because of something outside its control (what the user wanted wasn't available, the user left), it isn't `skipped`.
    - Voice transcripts come from speech recognition and contain mistakes. Don't count a garbled word against the agent, and don't count a repeated or cut-off sentence as a second action.
    - Conversations can be in any language or mix languages; judge what was said, not the language it was said in.
    - The agent's prompt may have filled in placeholders (shown as `{{name}}`) with real values; treat them as the corresponding real value.
    """

    transcript: str = dspy.InputField(desc="The conversation; turns are numbered")
    procedure: str = dspy.InputField(desc="The whole procedure, with each rule's key")
    rules_to_check: list[str] = dspy.InputField(desc="The keys of the rules to judge; judge only these")
    rules: list[RuleJudgment] = dspy.OutputField(desc="One entry per rule in rules_to_check, in that order")
    goal_reason: str = dspy.OutputField(desc="One sentence, in English")
    goal_reached: Literal["yes", "no", "unclear"] = dspy.OutputField()


# --- patterns ------------------------------------------------------------------------------------------------------

ASSIGN_INSTRUCTIONS = """You sort QA findings into known failure modes. Each finding is one conversation where an AI agent broke a rule of a procedure (SOP), with the checker's reason and quotes from the conversation. Pick the failure mode of the finding's own rule that describes what the agent did wrong, or `other` if none fits. Pick a mode only when its description matches the mechanism of the failure, not just the topic of the conversation."""


def assign_signature(keys: list[str], modes: dict[str, str]) -> type[dspy.Signature]:
    option = Choice[(*((k, d) for k, d in modes.items()), ("other", "None of these failure modes describes what the agent did wrong."))]
    fields: dict[str, Any] = {
        "rule": (str, dspy.InputField(desc="The rule the findings broke")),
        "findings": (dict[str, dict[str, Any]], dspy.InputField(desc="The findings, by key: status, reason and quotes")),
    }
    for k in keys:
        fields[k] = (option, dspy.OutputField(desc=f"The failure mode of finding {k}."))
    return dspy.make_signature(fields, ASSIGN_INSTRUCTIONS, "AssignFailureModes")


def assign_question(key: str) -> dict[str, Any]:
    return {"instructions": {"question": f"Which failure mode describes what the agent did wrong in `inputs.findings.{key}` (how it broke `inputs.rule`)? Match the mechanism of the failure, not the topic of the conversation."}}


class FindingIn(BaseModel):
    key: str
    status: str
    reason: str
    quotes: list[str]


class FailureMode(BaseModel):
    name: str = Field(description="At most 8 words: what the agent did.")
    description: str = Field(description="One or two sentences.")
    findings: list[str] = Field(description="Keys of the findings in this mode.")


class ClusterFindings(dspy.Signature):
    """You review QA findings from conversations between an AI agent (voice call or text chat) and users. Every finding is one conversation where the agent broke the same rule of a procedure (SOP): a step it skipped or did differently, a "never" rule it broke, or a warning sign it didn't act on. Each finding has the checker's reason and quotes from the conversation.

    Your job: split these findings into the distinct ways the agent failed this rule (failure modes), so that a person fixing the agent can see what to change.

    - Group by what the agent actually did wrong (the mechanism), not by the topic of the conversation, the item, or the user. Two findings belong together when the same fix to the agent's instructions would address both.
    - Give at most 5 failure modes. Fewer is better when the findings really are one failure: if they all fail the same way, return one mode.
    - A mode needs at least 2 findings. Findings that fit no mode stay unassigned; don't force them in.
    - Name each mode in at most 8 words, as what the agent did, in plain words (for example "Pitched the add-on in several sentences", "Never asked for the caller's name"). Don't repeat the rule's text as the name, and don't use rule numbers.
    - Describe each mode in one or two sentences: what the agent does, and when it happens.
    - Write names and descriptions in English, whatever the conversations' language.
    - List each finding by its key in at most one mode.
    """

    rule: str = dspy.InputField(desc="The rule the findings broke")
    findings: list[FindingIn] = dspy.InputField()
    modes: list[FailureMode] = dspy.OutputField(desc="At most 5")


class NameRulePattern(dspy.Signature):
    """You name a QA pattern: every conversation where an AI agent (voice call or text chat) broke one rule of a procedure (SOP). People scan a list of these names to see what keeps going wrong, so the name says what the agent did wrong, in a few plain words.

    - Name the failure, not the rule: what the agent did (a "never" rule broken), didn't do (a step missed), or didn't act on (a warning sign), in the past tense. For example, the never rule "Never volunteer prices or descriptions of menu items" becomes "Volunteered a price or description"; the step "Read the whole order back before you confirm it" becomes "Didn't read the order back"; the warning sign "The caller mentions anaphylaxis or an EpiPen: transfer to staff" becomes "Didn't transfer an allergy emergency".
    - At most 6 words. Plain spoken English, whatever the rule's language. No rule numbers, no quotes, no period at the end.
    - Keep the rule's meaning: don't add details or exceptions it doesn't state, and keep placeholders like {{name}} out of the name.
    """

    kind: Literal["step", "never", "warning"] = dspy.InputField(desc="step (the agent must do it), never (the agent must not), warning (a situation to act on)")
    rule: str = dspy.InputField(desc="The rule as the procedure states it")
    procedure: str = dspy.InputField(desc="The procedure's name, for context")
    name: str = dspy.OutputField(desc="At most 6 words: what the agent did wrong")


class PatternIn(BaseModel):
    key: str
    rule: str
    findings: int
    failure_modes: list[str]


class ThemeIn(BaseModel):
    key: str
    name: str
    description: str
    patterns: list[str] = Field(description="The patterns in it now.")


class ThemeGroup(BaseModel):
    key: str = Field(description="An existing theme's key, or new.")
    name: str = Field(description="At most 6 words.")
    description: str = Field(description="One sentence.")
    patterns: list[str] = Field(description="Pattern keys.")


class GroupThemes(dspy.Signature):
    """You organize QA results for one procedure (SOP) that an AI agent follows in conversations with users (voice calls or text chats). Each pattern is one rule of the procedure that agents break, with the named failure modes seen so far.

    Group the patterns into themes by the phase of the conversation where the failure happens (for example: opening, collecting the request, offers, confirming, closing, handling difficult users). Themes help a person read a report from the start of a conversation to its end.

    - Use between 1 and 6 themes. Every pattern goes in exactly one theme.
    - Order the themes as the phases happen in a conversation; a theme about situations that can happen at any point goes last.
    - Name each theme in at most 6 words, in plain English, by the phase or situation (for example "Taking the order", "Closing the order", "Hard-to-understand callers"). Describe it in one sentence: what goes wrong there.
    - Existing themes are listed with their keys. Keep an existing theme (give its key, and keep its name) whenever it still fits its patterns, so names stay stable for the people reading them. Use the key `new` only for a theme none of the existing ones covers.
    """

    procedure: str = dspy.InputField()
    patterns: list[PatternIn] = dspy.InputField()
    existing_themes: list[ThemeIn] = dspy.InputField()
    themes: list[ThemeGroup] = dspy.OutputField(desc="In conversation order")


# --- suggestions -------------------------------------------------------------------------------------------------------


class ExampleIn(BaseModel):
    status: str = Field(description="The rule's status in that conversation.")
    reason: str = Field(description="The checker's reason.")
    quotes: list[str] = Field(description="What was said.")


class SuggestEdit(dspy.Signature):
    """You maintain the instructions of AI agents (voice calls and text chats). Instructions are kept as sopc files; a procedure (SOP) file has a name, a goal (`description`), when it applies (`scope`), guidance, ordered steps (`procedureSteps`), things never to do (`forbiddenActions`) and warning signs (`warningSigns`). QA found a pattern: the same thing keeps happening on one rule of a procedure, and a reviewer gave it a verdict. Propose the smallest edit to the procedure file that settles it.

    By verdict:
    - `real`: the agent makes a real mistake. Change the instructions so the agent stops making it: make the rule clearer or more specific, add a step, or add guidance that names the situation in the examples. Don't weaken the rule.
    - `acceptable`: what the agent did is fine in this situation. Add the exception to the rule (or to the guidance next to it), naming the situation precisely, so the same behavior in that situation no longer breaks it. Keep the rule for every other situation.
    - `sop_wrong`: the rule itself is wrong or outdated. Edit the rule so it says what the agent should do now; the reviewer's note says why, when there is one.

    Rules:
    - Return the whole file in `new_text`, in the same format (YAML or Markdown) and layout as `procedure_file`. Change only what the verdict needs; keep every other line, every `{{placeholder}}`, and every `tool:` and `required:` exactly as written.
    - Keep the rule at its position in its list, so the other rules keep their numbers. Don't add a list item unless the fix needs one.
    - Don't mention QA, patterns, reviewers or this edit in the file. Write instructions, in the file's language.
    - `block_id`: the id of the block you edited, `procedure_id`.
    - `rationale`: one or two sentences for the reviewer, in English: what you changed and why it settles the pattern.
    - If `changes_since` isn't empty, an earlier suggestion for this pattern was written against an older version of the file, and someone has edited the file since (the diff, older to current). `procedure_file` already has those edits. Keep them and build on them: don't undo, reword or move what they changed, unless the verdict is about exactly that text.
    - If `previous_error` isn't empty, your last proposal (`previous_text`) didn't compile; fix that problem.
    """

    procedure_id: str = dspy.InputField()
    procedure_file: str = dspy.InputField(desc="The procedure's file as it is now")
    rule: str = dspy.InputField(desc="The rule the pattern is about, with its kind and number")
    pattern: str = dspy.InputField(desc="The pattern's name and description")
    verdict: Literal["real", "acceptable", "sop_wrong"] = dspy.InputField()
    note: str = dspy.InputField(desc="The reviewer's note; may be empty")
    examples: list[ExampleIn] = dspy.InputField(desc="Up to 5 conversations in the pattern")
    changes_since: str = dspy.InputField(desc="Edits made to the file since an earlier suggestion for this pattern was written, as a unified diff; else empty")
    previous_text: str = dspy.InputField(desc="Your last proposal, if it didn't compile; else empty")
    previous_error: str = dspy.InputField(desc="Why it didn't compile; else empty")
    block_id: str = dspy.OutputField()
    new_text: str = dspy.OutputField(desc="The whole procedure file, edited")
    rationale: str = dspy.OutputField()


# --- onboarding ------------------------------------------------------------------------------------------------------


class Unit(BaseModel):
    n: int
    text: str


class SegmentPrompt(dspy.Signature):
    """You are splitting one voice agent's existing system prompt into the parts of a sopc project. The team runs several agents whose prompts were copied and edited by hand; sopops will store shared text once and compose each agent from it. You decide what each part of the prompt is. You never rewrite text: you only group numbered units and label them.

    Return segments that together cover every unit exactly once, in order. Each segment is one or more consecutive units about one topic, with:

    - `units`: the unit numbers, consecutive.
    - `kind`: one of
      - `context`: facts about this agent only: its location, address, hours, owner, what it serves, payment, parking, anything that would be false for a sibling agent. A sentence naming the agent ("Tony's is a pizza shop in Brooklyn") is usually context.
      - `instruction`: text several agents could share as is, or with a value swapped: identity and role ("You are the phone host for X. You take orders ..."), tone and voice, policies, general rules, what to say before hanging up. The agent's name inside an otherwise generic sentence doesn't make it context.
      - `procedure`: a situation and what to do in it: "When the caller wants to book a table: ask for ..., then check ...". Steps, things never to do in that situation, and signs that call for a transfer. Include the procedure's own heading (e.g. `## Reservations`) in its segment.
      - `heading`: a heading that only introduces other sections, such as `# Procedures` or `## How to handle calls`, with no instructions of its own.
    - `title`: a short name for the topic, the same words you would use for the same passage in any agent: "Identity", "Brand voice", "Allergies", "Reservations", "Delivery", "Closing".

    How to decide:
    - A passage is a procedure when it describes a trigger and the actions for it. A list of facts or tone rules is not a procedure, even if it is a list.
    - Keep one topic per segment. Split where the topic changes, even inside a run of short paragraphs; join consecutive units that belong to the same procedure (its heading, trigger, steps and rules).
    - Unsure between context and instruction: choose instruction if the sentence would still be true for another location of the same business with only the name or a number changed; otherwise context.
    - Unsure between procedure and instruction: choose instruction.
    - Text said only at the end of the call ("Before hanging up, repeat the total") is an instruction with its own segment.
    - Don't drop units. Every unit number appears in exactly one segment.
    - This import creates reusable blocks. Use consistent topic boundaries and titles for equivalent passages across agents so sopops can store repeated text once. Keep each source's wording, ordering, placeholders and behavior intact. Never merge passages that differ in a rule, condition, tool, limit, timing or exception: label them as the same topic so sopops reports the difference for review instead of silently choosing one.

    The user's guidance:
    - `guidance` says how the user wants their instructions organized ("Keep each restaurant's menu as its own block", "Everything about refunds is one procedure", "Don't split the greeting out"). When it's given, follow it: it outranks the default choices above about where to split, how to label a passage and what to title it. Use the same titles it uses for the topics it names.
    - It never changes what's in the prompt: still cover every unit exactly once, in order, never drop, rewrite or invent text, and still keep passages that differ in a rule apart. If it asks for something these rules don't allow, follow it as far as they do.
    - Empty guidance: use the default choices.
    """

    agent: str = dspy.InputField(desc="The agent's name")
    platform: str = dspy.InputField()
    tools: list[str] = dspy.InputField(desc="Tools the agent can call")
    units: list[Unit] = dspy.InputField(desc="The prompt, as numbered units (paragraphs), in order")
    guidance: str = dspy.InputField(desc="How the user wants their instructions organized; may be empty")
    segments: list[Segment] = dspy.OutputField()


class StructureProcedure(dspy.Signature):
    """You are turning one passage of an agent prompt into a sopc procedure (an SOP): a goal, when it applies, ordered steps, things never to do, and warning signs. The passage may contain `{{placeholders}}`; keep each one exactly as written, inside the field where its text goes.

    Fields:
    - `name`: a short title, from the passage's own heading if it has one ("Reservations", "Large orders").
    - `when`: the situation that triggers it, in the passage's words, without a leading "When" or "If" and without a trailing colon. "When the caller wants to book a table:" -> "The caller wants to book a table."
    - `goal`: the outcome that means it worked. Use the passage's words if it states one. If it doesn't, write one plain sentence ("The caller has a confirmed table, or knows why one isn't available.") and set `goal_inferred` to true.
    - `steps`: the ordered actions, one per item, in the original wording, without list numbers. If a step uses one of the agent's tools, set `tool` to its exact name from the tool list. Set `required` only when the passage says the call must always happen ("always", "every time", "before you confirm").
    - `never`: "never / don't / do not" rules for this situation, verbatim.
    - `warning_signs`: "if the caller says X, transfer / escalate" rules, verbatim, with `tool` set if the rule names a transfer tool.
    - `guidance`: any other sentence of the passage that isn't one of the above, verbatim. Leave empty if there is none.

    Rules:
    - Preserve wording. Don't improve, shorten, merge or add instructions. The only edits allowed: dropping list numbers and bullets, dropping the heading (it becomes `name`), and dropping "When"/"If" and the colon from the trigger.
    - Every sentence of the passage must land in exactly one field. If you can't place a sentence, put it in `guidance`.
    - Only name tools from the list you're given; never invent a tool name.
    - `guidance` says how the user wants their instructions organized. When it's given, follow it where it bears on this procedure (its name, what counts as one step, which field a sentence goes in): it outranks the default choices above. It never allows dropping, rewording or inventing text, or a tool not on the list. Empty guidance: use the default choices.
    """

    title: str = dspy.InputField(desc="The passage's topic")
    tools: list[str] = dspy.InputField(desc="The tools the agents using it can call")
    passage: str = dspy.InputField()
    guidance: str = dspy.InputField(desc="How the user wants their instructions organized; may be empty")
    procedure: ProcedureDraft = dspy.OutputField()


class Slot(BaseModel):
    n: int
    text: str = Field(description="The shared text around the slot.")
    values: dict[str, str] = Field(description="Each agent's value in the slot.")


class NameVariables(dspy.Signature):
    """Several agents shared the same text except for a few values, so the text now has slots, one per place where the agents differ. Name each slot as a sopc variable: snake_case, lowercase letters, digits and underscores, starting with a letter, saying what the value is, not what it is now: `restaurant_name`, `menu_link`, `staff_transfer`, `upsell_limit`, `opening_hours`. The same kind of value in two slots gets the same name only if every agent has the same value in both.

    Return one name per slot, in the order given."""

    slots: list[Slot] = dspy.InputField()
    names: list[str] = dspy.OutputField(desc="One snake_case name per slot, in order")


class KeyCheck(dspy.Signature):
    """Answer the question about the sentence."""

    sentence: str = dspy.InputField()
    answer: Noul = dspy.OutputField(desc="Is the sentence about the weather?")
