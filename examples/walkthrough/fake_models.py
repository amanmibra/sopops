"""Fake mode: scripted stand-ins for every model call, so the walkthrough runs offline, free and deterministic.

This is not a model and doesn't pretend to be one. It's a keyword checker that knows the phrasings the walkthrough's
generator uses, wired in through `sopops_server.ai.fakes` so the real pipeline runs around it: routing, rule checks, rollup,
findings, patterns (split, assignment, reclustering), themes, Insights. What it shows is the plumbing and the UI on
data with a known answer; how well a real model finds the failures is what real mode is for.

- Routing (a decision): a procedure applies when its topic comes up (allergies, delivery, a table).
- Rule checks (`CheckRules`, the reviewer, in `SOPOPS_CHECK_MODE=review`): one detector per rule, matched by the
  rule's text, so a reworded rule gets the detector for its new wording. A step's OR options (reservations: book,
  change or cancel) apply by what the caller asked for; the others are not applicable. It is deliberately naive about one thing:
  it flags the word "safe" even when the agent says it can't promise something is safe, which gives a
  "checker wrong" verdict something to act on.
- Clustering (`ClusterFindings`): findings with the same reason form one failure mode; assignment (a decision) picks
  the mode whose name is the finding's reason. Themes: one per rule kind. Rule patterns (`NameRulePattern`) get a
  scripted name for each of the walkthrough's rules, and the rule's text trimmed for any other.
- Onboarding's extraction (`SegmentPrompt`, `StructureProcedure`, `NameVariables`) is answered by sopops's own
  model-free rules mode (`sopops_server.onboarding.rules`), so Import → Paste works too.
- Any other signature (one added after this was written) gets a plain reply built from its output fields' types.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import dspy
from pydantic import BaseModel

from sopops_server.ai.fakes import FakeModels, FakeSystemOne, default_reply
from sopops_server.ai.signatures import (
    CheckRules,
    ClusterFindings,
    GroupThemes,
    NameRulePattern,
    NameVariables,
    SegmentPrompt,
    StructureProcedure,
    SuggestEdit,
)
from sopops_server.onboarding.rules import RulesModels

TOPICS = {
    "allergen": re.compile(r"allerg|celiac|epipen|anaphyla", re.I),
    "deliver": re.compile(r"deliver", re.I),
    "reservation": re.compile(r"\b(table|reservation|book(ing)?)\b", re.I),
}
LINE = re.compile(r"^\[(\d+)\]\s+\S+\s+(AGENT|USER|TOOL|SYSTEM): (.*)$")
RULE = re.compile(r"^- (step|never|warning)_(\d+)(_or_\d+)?: (?:OR )?(.*?)(?: \(tool: [^)]*\))?$")

SAFE = re.compile(r"\b(?:totally|perfectly|completely|absolutely) safe\b|\bsafe for (?:you|him|her|them|your \w+)\b", re.I)
FREE_KITCHEN = re.compile(r"\b(?:nut|peanut|allergen|gluten)-free kitchen\b|kitchen is (?:completely |totally |a )?(?:nut|peanut|allergen|gluten)-free", re.I)
SAFE_WORD = re.compile(r"\bsafe\b", re.I)
ETA = re.compile(r"\b\d{2,3} (?:minutes|mins)\b|\bby \d{1,2}(?::\d{2})?\s?(?:pm|am)\b", re.I)
LARGE_PARTY = re.compile(r"\b(?:party of|table for|booking for|for) (9|1\d|2\d|nine|ten|eleven|twelve|fourteen|fifteen|sixteen|twenty)\b", re.I)
CONFIRMED = re.compile(r"\b(?:you're all set|you are all set|is confirmed|that's booked|you're booked|booked you)\b", re.I)
EMERGENCY = re.compile(r"anaphyla|epipen|epi-pen", re.I)
CANCEL = re.compile(r"\bcancel\b", re.I)
ASK_ADDRESS = re.compile(r"\baddress\b.*\?", re.I)
READBACK = re.compile(r"\bThat's .+, \$[\d.]+ with the delivery fee\b")
CHANGE = re.compile(r"\b(?:move it|change my reservation)\b", re.I)

# Reasons double as failure-mode names, so clustering and assignment can match them.
REASONS = {
    "safe": "Told the caller a dish is safe for their allergy",
    "free_kitchen": "Claimed the kitchen is free of the allergen",
    "safe_word": "Used the word safe while declining to promise it",
    "no_lookup": "Answered the allergy question without the allergen list",
    "no_transfer": "Kept going after the caller mentioned anaphylaxis",
    "eta": "Gave the caller a delivery time",
    "eta_early": "Gave a delivery time before checking the zone",
    "no_zone": "Took a delivery address without checking the zone",
    "late_zone": "Read back the order before checking the zone",
    "large_party": "Confirmed a large party without the manager",
    "no_availability": "Booked without checking availability",
    "no_change": "Didn't change the booking",
    "no_cancel": "Didn't cancel the booking",
    "wrong_option": "Moved the booking when the caller wanted to cancel",
}
# A step's OR options, by words in the option: (what the caller asked for, its tool, reason when it wasn't called).
OPTIONS = {
    "booking a new": ("book", "check_reservations", "no_availability"),
    "changing a booking": ("change", "change_reservation", "no_change"),
    "cancelling": ("cancel", "cancel_reservation", "no_cancel"),
}
# Steps that need a tool, by words in the step: (tool, reason when it wasn't called).
STEP_TOOLS = {
    "check each item": ("lookup_allergens", "no_lookup"),
    "delivery zone": ("check_delivery_zone", "no_zone"),
    "availability": ("check_reservations", "no_availability"),
}


class Line(BaseModel):
    turn: int
    speaker: str
    text: str


def parse_transcript(text: str) -> list[Line]:
    out = []
    for raw in text.splitlines():
        m = LINE.match(raw)
        if m:
            out.append(Line(turn=int(m.group(1)), speaker=m.group(2), text=m.group(3)))
    return out


def parse_rules(procedure: str) -> dict[str, str]:
    """{rule key: rule text} from `render_procedure`'s output."""
    out = {}
    for raw in procedure.splitlines():
        m = RULE.match(raw.strip())
        if m:
            out[f"{m.group(1)}_{m.group(2)}{m.group(3) or ''}"] = m.group(4)
    return out


def first(lines: list[Line], speaker: str, pattern: re.Pattern[str]) -> Line | None:
    return next((x for x in lines if x.speaker == speaker and pattern.search(x.text)), None)


def tool_turn(lines: list[Line], name: str) -> Line | None:
    return next((x for x in lines if x.speaker == "TOOL" and x.text.startswith(name)), None)


def asked_for(lines: list[Line]) -> str:
    """What a reservations caller wants: book, change or cancel."""
    return "cancel" if first(lines, "USER", CANCEL) else "change" if first(lines, "USER", CHANGE) else "book"


def judge_option(key: str, rule: str, lines: list[Line]) -> dict[str, Any]:
    """One of a step's OR options: not applicable unless it's what the caller asked for; then done if its tool was called."""
    want, tool, why = next((v for k, v in OPTIONS.items() if k in rule.lower()), ("", "", ""))
    if want != asked_for(lines):
        return {"rule": key, "status": "not_applicable", "reason": "The caller didn't ask for this.", "evidence": []}
    if hit := tool_turn(lines, tool):
        return {"rule": key, "status": "done", "reason": f"Called {tool}.", "evidence": [{"turn": hit.turn, "quote": hit.text}]}
    if want == "cancel" and (hit := tool_turn(lines, "change_reservation")):
        return {"rule": key, "status": "skipped", "reason": REASONS["wrong_option"], "evidence": [{"turn": hit.turn, "quote": hit.text}]}
    return {"rule": key, "status": "skipped", "reason": REASONS[why], "evidence": []}


def judge(key: str, rule: str, lines: list[Line]) -> dict[str, Any]:
    """One rule's status, reason and quotes, by the rule's wording."""
    if "_or_" in key:
        return judge_option(key, rule, lines)
    kind = key.split("_")[0]
    status = {"step": "done", "never": "ok", "warning": "absent"}[kind]
    reason, hit = "Nothing in the transcript goes against it.", None
    text = rule.lower()

    def bad(st: str, why: str, line: Line | None) -> None:
        nonlocal status, reason, hit
        status, reason, hit = st, why, line

    if kind == "never" and ("allergen-free" in text or "safe" in text):
        if line := first(lines, "AGENT", SAFE):
            bad("violated", REASONS["safe"], line)
        elif line := first(lines, "AGENT", FREE_KITCHEN):
            bad("violated", REASONS["free_kitchen"], line)
        elif line := first(lines, "AGENT", SAFE_WORD):
            bad("violated", REASONS["safe_word"], line)  # the naive part: a disclaimer is not a claim
    elif kind == "step" and (tool := STEP_TOOLS.get(next((k for k in STEP_TOOLS if k in text), ""))):
        if not tool_turn(lines, tool[0]):
            if tool_turn(lines, "transfer_to_staff"):
                status, reason = "not_reached", "The call was transferred before this step."
            else:
                bad("skipped", REASONS[tool[1]], None)
    elif kind == "step" and "delivery address" in text:
        if line := first(lines, "AGENT", ASK_ADDRESS):
            reason, hit = "Asked for the delivery address.", line
    elif kind == "step" and "read back the order" in text:
        if line := first(lines, "AGENT", READBACK):  # its turn is what puts it in or out of order (the rollup's)
            zone = tool_turn(lines, "check_delivery_zone")
            reason, hit = (REASONS["late_zone"] if zone and line.turn < zone.turn else "Read back the order and the total."), line
    elif kind == "warning" and "anaphylaxis" in text:
        if user := first(lines, "USER", EMERGENCY):
            if tool_turn(lines, "transfer_to_staff"):
                status, reason, hit = "present", "The caller mentioned anaphylaxis and was transferred.", user
            else:
                bad("unheeded", REASONS["no_transfer"], user)
    elif kind == "never" and "delivery time" in text:
        eta = first(lines, "AGENT", ETA)
        zone = tool_turn(lines, "check_delivery_zone")
        if eta and "before" not in text:
            bad("violated", REASONS["eta"], eta)
        elif eta and (zone is None or eta.turn < zone.turn):
            bad("violated", REASONS["eta_early"], eta)
    elif kind == "never" and "8 guests" in text:
        big = first(lines, "USER", LARGE_PARTY)
        done = first(lines, "AGENT", CONFIRMED)
        if big and done and not tool_turn(lines, "transfer_to_staff"):
            bad("violated", REASONS["large_party"], done)
    evidence = [{"turn": hit.turn, "quote": hit.text}] if hit else []
    return {"rule": key, "status": status, "reason": reason, "evidence": evidence}


def check_rules(inputs: dict[str, Any]) -> dict[str, Any]:
    lines = parse_transcript(inputs["transcript"])
    rules = parse_rules(inputs["procedure"])
    return {"rules": [judge(k, rules.get(k, ""), lines) for k in inputs["rules_to_check"]], "goal_reason": "Scripted check.", "goal_reached": "yes"}


def headline(reason: str) -> str:
    """A finding's reason up to its first sentence end or the note in parentheses sopops adds (a required tool step's)."""
    return reason.removeprefix("Done out of order (after a later step). ").split(".")[0].split(" (")[0].strip()


def cluster(inputs: dict[str, Any]) -> dict[str, Any]:
    by_reason: dict[str, list[str]] = defaultdict(list)
    for f in inputs["findings"]:
        by_reason[headline(f.reason)].append(f.key)
    modes = [{"name": r, "description": f"{r}.", "findings": keys} for r, keys in sorted(by_reason.items(), key=lambda kv: -len(kv[1])) if len(keys) >= 2]
    return {"modes": modes[:5]}


def themes(inputs: dict[str, Any]) -> dict[str, Any]:
    existing = {t.name: t.key for t in inputs["existing_themes"]}
    names = {"Step": "Doing the steps", "Never": "Things said that shouldn't be", "Warning": "Situations to escalate"}
    groups: dict[str, list[str]] = defaultdict(list)
    for p in inputs["patterns"]:
        groups[p.rule.split(" ")[0]].append(p.key)
    order = ["Step", "Never", "Warning"]
    out = []
    for kind in sorted(groups, key=lambda k: order.index(k) if k in order else 9):
        name = names.get(kind, kind)
        out.append({"key": existing.get(name, "new"), "name": name, "description": f"{name}.", "patterns": groups[kind]})
    return {"themes": out}


# Rule patterns' names, by words in the rule: what the agent did wrong.
RULE_NAMES = [
    (("allergen-free", "safe"), "Called a dish safe for an allergy"),
    (("check each item",), "Didn't check the allergen list"),
    (("anaphylaxis",), "Didn't transfer an allergy emergency"),
    (("delivery time",), "Gave a delivery time"),
    (("delivery zone",), "Didn't check the delivery zone"),
    (("read back the order",), "Read back the order out of turn"),
    (("8 guests",), "Booked a large party without the manager"),
    (("availability",), "Booked without checking availability"),
    (("changing a booking",), "Didn't change the booking"),
    (("cancelling",), "Didn't cancel when asked"),
]


def name_rule(inputs: dict[str, Any]) -> dict[str, Any]:
    rule = inputs["rule"].lower()
    for words, name in RULE_NAMES:
        if any(w in rule for w in words):
            return {"name": name}
    return default_reply(NameRulePattern, inputs) or {"name": inputs["rule"]}


# Scripted edits for the walkthrough's two suggestions; any other gets a generic one of the same shape.
REWORD = {"Never tell the caller a delivery time": "Never tell the caller a delivery time before the delivery zone check confirms the address"}
GUIDANCE = {
    "Allergen check": "If the caller asks whether a dish is safe, tell them what the allergen list shows and that everything is made "
                      "in a shared kitchen. Don't reassure them, and don't describe the kitchen as free of any allergen.",
    "Reservations": "For a party of more than eight, check availability, then transfer the caller to {{staff_transfer}} to confirm. "
                    "Don't confirm the booking yourself, even when there's room.",
}


def suggest(inputs: dict[str, Any]) -> dict[str, Any]:
    """`SuggestEdit`: an exception added to the rule (acceptable, sop_wrong), or guidance under the goal (real). Edits
    the file it's given, so a rewrite (`changes_since`) keeps what changed since."""
    text: str = inputs["procedure_file"]
    lines = text.split("\n")
    if inputs["verdict"] in ("acceptable", "sop_wrong"):
        for i, line in enumerate(lines):
            body = line.removeprefix("- ")
            if line.startswith("- ") and body.split(" `")[0] in REWORD:
                lines[i] = "- " + REWORD[body.split(" `")[0]] + body[len(body.split(" `")[0]):]
                break
        else:
            for i, line in enumerate(lines):
                if line.startswith("- ") and line[2:].split(" `")[0].strip() and line[2:].split(" `")[0] in inputs["rule"]:
                    lines[i] = line + f" (fine when: {inputs['pattern'].split('.')[0].lower()})"
                    break
        why = "Narrowed the rule to the case it was written for; the flagged behavior is fine."
    else:
        title = next((x[2:].strip() for x in lines if x.startswith("# ")), "")
        paragraph = GUIDANCE.get(title, "")
        if not paragraph or paragraph in text:  # the procedure's scripted guidance is there already (step 9's suggestions)
            paragraph = f"Watch for this: {inputs['pattern'].split('.')[0].lower()}."
        at = next((i for i, x in enumerate(lines) if x.startswith("**When:**")), 1)
        lines[at + 1 : at + 1] = ["", paragraph]
        why = "Added guidance that names the situation, so the agent stops making the mistake."
    if inputs.get("changes_since"):  # a rewrite: `procedure_file` already has the edits made since, and they stay
        why = "Kept the edits made since the last suggestion. " + why
    return {"block_id": inputs["procedure_id"], "new_text": "\n".join(lines), "rationale": why}


def plain(signature: type[dspy.Signature]) -> dict[str, Any]:
    """A reply for a signature this module doesn't know: empty values of each output field's type."""
    out: dict[str, Any] = {}
    for name, f in signature.output_fields.items():
        t = f.annotation
        origin = getattr(t, "__origin__", None)
        out[name] = [] if origin is list or t is list else {} if origin is dict or t is dict else "" if t is str else None
    return out


class WalkthroughModels(FakeModels):
    """`sopops_server.ai.fakes.FakeModels` with the walkthrough's scripted replies."""

    def __init__(self) -> None:
        super().__init__(self.respond, FakeSystemOne(self.decide, model="fake-decisions"), {"fast": "fake/fast", "reasoning": "fake/reviewer"})
        self.rules = RulesModels()

    def respond(self, role: str, signature: type[dspy.Signature], inputs: dict[str, Any]) -> dict[str, Any]:
        if signature is CheckRules:
            return check_rules(inputs)
        if signature is ClusterFindings:
            return cluster(inputs)
        if signature is GroupThemes:
            return themes(inputs)
        if signature is SuggestEdit:
            return suggest(inputs)
        if signature is NameRulePattern:
            return name_rule(inputs)
        if signature in (SegmentPrompt, StructureProcedure, NameVariables):
            return dict(self.rules.predict(role, signature, **inputs).items())
        return plain(signature)

    def decide(self, state: dict[str, Any], questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        inputs = state.get("inputs", {})
        if "findings" in inputs:  # assignment: the mode named by the finding's reason
            out = {}
            for key, q in questions.items():
                reason = headline(str(inputs["findings"][key].get("reason", "")))
                out[key] = next((label for label, desc in q["criteria"].items() if desc and reason and desc.startswith(reason)), "other")
            return out
        if "procedures" in inputs:  # routing
            said = " ".join(u.get("text", "") for u in inputs.get("transcript", []) if u.get("speaker") == "user")
            out = {}
            for key in questions:
                brief = inputs["procedures"].get(key.rsplit("_", 1)[0], {})
                topic = next((t for t in TOPICS if t in f"{brief.get('name', '')} {brief.get('when', '')}".lower()), None)
                out[key] = 0.95 if topic and TOPICS[topic].search(said) else 0.03
            return out
        return {k: 0.5 for k in questions}
