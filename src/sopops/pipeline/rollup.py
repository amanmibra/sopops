"""Rule statuses and how they roll up into a procedure result. Pure functions, no I/O.

Rule statuses:
- steps: done | out_of_order | skipped | not_reached | not_applicable
- nevers: violated | ok
- warning signs: present | absent (a condition, never a pass or fail)

The model judges done / skipped / not_reached / not_applicable. `out_of_order` is worked out here, from the first
evidence turn of each done step: the fewest steps that explain the disorder (the ones outside a longest
non-decreasing run of turns) are out of order.

Required tool steps (`tool: x` + `required`) are checked against the session's tool calls when it has any: called
means done (out of order if the turns say so), not called means skipped, unless the model found the step
not_reached or not_applicable. A session without tool calls falls back to the model's reading of the transcript,
and the evidence says which source it came from.

Procedure result:
- `deviated`: a never rule was violated, a required step was skipped, or a step was skipped and the goal wasn't
  reached (or it's unclear whether it was).
- `partial`: otherwise, a step was out of order or not reached, or a step that isn't required was skipped but the
  goal was reached anyway (the leniency rule: the agent got there another way, so it's a gap, not a deviation).
- `followed`: every applicable step done in order, no never violated.
- `not_measurable`: nothing to judge: no applicable step and no never violated, or the agent never spoke.
Warning signs never change the result.

Findings are rule results with status skipped, violated or out_of_order: each is a concrete thing the agent did or
didn't do that a person might want to fix. not_reached isn't (the session ended), and neither is a present warning
sign. A skipped step that the leniency rule kept from making the procedure deviated is still a finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

STEP_STATUSES = ("done", "out_of_order", "skipped", "not_reached", "not_applicable")
NEVER_STATUSES = ("violated", "ok")
WARNING_STATUSES = ("present", "absent")
JUDGED_STEP_STATUSES = ("done", "skipped", "not_reached", "not_applicable")  # what the model picks from
FINDING_STATUSES = frozenset({"skipped", "violated", "out_of_order"})
RESULTS = ("deviated", "partial", "followed", "not_measurable")
SEVERITY = {"deviated": 3, "partial": 2, "followed": 1, "not_measurable": 0}


@dataclass
class Judged:
    """One rule's status as stored."""

    rule_id: str
    kind: str
    index: int
    status: str
    evidence: list[dict[str, Any]]
    reason: str
    required: bool = False


@dataclass
class Rollup:
    result: str
    steps_applicable: int
    steps_done: int
    nevers_violated: int


def first_turn(evidence: list[dict[str, Any]]) -> int | None:
    turns = [e["turn"] for e in evidence if isinstance(e.get("turn"), int)]
    return min(turns) if turns else None


def mark_out_of_order(steps: list[Judged]) -> None:
    """Steps (in index order) that are done but outside a longest non-decreasing run of first-evidence turns become
    out_of_order. Done steps without a turn are left alone."""
    done = [(i, first_turn(s.evidence)) for i, s in enumerate(steps) if s.status == "done"]
    seq = [(i, t) for i, t in done if t is not None]
    n = len(seq)
    if n < 2:
        return
    best = [1] * n
    prev = [-1] * n
    for j in range(n):
        for k in range(j):
            if seq[k][1] <= seq[j][1] and best[k] + 1 > best[j]:
                best[j], prev[j] = best[k] + 1, k
    end = max(range(n), key=lambda j: (best[j], -j))
    keep = set()
    while end != -1:
        keep.add(seq[end][0])
        end = prev[end]
    for i, _ in seq:
        if i not in keep:
            steps[i].status = "out_of_order"
            steps[i].reason = f"Done out of order (after a later step). {steps[i].reason}".strip()


def apply_tool_calls(steps: list[Judged], tools: dict[str, str | None], tool_calls: list[dict[str, Any]], turns: list[dict[str, Any]]) -> None:
    """Required tool steps against the tool-call log. `tools` maps rule_id to its tool name (required steps only).
    Does nothing when the session has no tool calls."""
    if not tool_calls:
        for s in steps:
            if s.rule_id in tools:
                for e in s.evidence:
                    e.setdefault("source", "transcript")
        return
    for s in steps:
        name = tools.get(s.rule_id)
        if not name:
            continue
        calls = [c for c in tool_calls if c.get("name") == name]
        if calls:
            c = calls[0]
            turn = next((i for i, t in enumerate(turns) if t.get("role") == "tool" and (t.get("name") == name or t.get("text") == name)), None)
            s.evidence = [{"turn": turn, "quote": f"{name}({_short(c.get('arguments'))})", "source": "tool_calls"}]
            errored = all(bool(x.get("is_error")) for x in calls)
            s.status = "done"
            s.reason = f"Called {name}{' (every call returned an error)' if errored else ''}."
        elif s.status not in ("not_reached", "not_applicable"):
            s.status = "skipped"
            s.evidence = []
            s.reason = f"{name} was never called (required)."


def rollup(rules: list[Judged], goal_reached: bool | None, agent_spoke: bool = True) -> Rollup:
    steps = [r for r in rules if r.kind == "step"]
    nevers = [r for r in rules if r.kind == "never"]
    applicable = [s for s in steps if s.status != "not_applicable"]
    done = [s for s in steps if s.status in ("done", "out_of_order")]
    violated = [n for n in nevers if n.status == "violated"]
    if not agent_spoke or (not applicable and not violated):
        return Rollup("not_measurable", len(applicable), len(done), len(violated))
    skipped = [s for s in steps if s.status == "skipped"]
    hard_skip = any(s.required for s in skipped) or (skipped and goal_reached is not True)
    if violated or hard_skip:
        result = "deviated"
    elif skipped or any(s.status in ("out_of_order", "not_reached") for s in steps):
        result = "partial"
    else:
        result = "followed"
    return Rollup(result, len(applicable), len(done), len(violated))


def lenient(rule: Judged, goal_reached: bool | None) -> bool:
    """A skipped step the leniency rule kept from making its procedure deviated."""
    return rule.kind == "step" and rule.status == "skipped" and not rule.required and goal_reached is True


def worst(results: list[str | None]) -> str | None:
    found = [r for r in results if r]
    return max(found, key=SEVERITY.__getitem__) if found else None


def _short(v: Any, n: int = 120) -> str:
    import json

    s = v if isinstance(v, str) else json.dumps(v, separators=(",", ":"), default=str)
    return s if len(s) <= n else s[: n - 1] + "…"
