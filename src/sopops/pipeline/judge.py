"""The two model calls: route (fast model) and check (reasoning model, one call per procedure, every rule at once).

Each reply is a flat list (one entry per procedure, or per rule) whose ids are an enum of exactly the ones in play.
The schema stays small, because a per-rule object schema can be too large for native structured output on big
procedures; a validator then checks that every one is answered exactly once and that each status fits its rule's
kind, and a reply that fails gets the one retry `llm.complete` allows.

Messages are laid out for prompt caching: the instructions (same for every call), then the transcript, then the
procedure, then the question. Breakpoints after the instructions and the transcript (route: after the procedure list,
which every session in a workspace shares) let the per-procedure checks of one session reuse the cached transcript.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from ..llm import LLM
from . import prompts
from .procedures import Procedure
from .rollup import JUDGED_STEP_STATUSES, NEVER_STATUSES, WARNING_STATUSES, Judged
from .transcript import render_procedure, render_route_procedure, render_transcript, verify_evidence

CACHE = {"type": "ephemeral"}
ROUTE_STATUSES = ("applied", "not_applicable", "ended_before_start", "ended_before_finish")
EXCLUDED_REASON = {
    "ended_before_start": "The session ended before the procedure started.",
    "ended_before_finish": "The session ended before the procedure finished.",
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Evidence(_Model):
    turn: int = Field(description="The turn number, as shown in the transcript.")
    quote: str = Field(description="Copied exactly from that turn's text; at most about 25 words.")




def _block(text: str, cache: bool = False) -> dict[str, Any]:
    b: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        b["cache_control"] = CACHE
    return b


# --- route ---------------------------------------------------------------------


@dataclass
class Routed:
    procedure_id: str
    status: str  # applied | not_applicable | ended_before_start | ended_before_finish
    reason: str

    @property
    def applicable(self) -> bool:
        return self.status != "not_applicable"

    @property
    def excluded_reason(self) -> str | None:
        return EXCLUDED_REASON.get(self.status)


def _complete_list(field: str, key: str, wanted: list[str], kinds: dict[str, tuple[str, ...]] | None = None):
    """A validator: `field` lists every id in `wanted` exactly once (by `key`), and each status fits its kind."""

    def check(self):
        items = getattr(self, field)
        got = [getattr(x, key) for x in items]
        missing, dupes = [w for w in wanted if w not in got], {g for g in got if got.count(g) > 1}
        if missing or dupes:
            raise ValueError(f"answer each of {', '.join(wanted)} exactly once (missing: {', '.join(missing) or 'none'}; repeated: {', '.join(sorted(dupes)) or 'none'})")
        if kinds:
            bad = [f"{getattr(x, key)}: {x.status}" for x in items if x.status not in kinds[getattr(x, key)]]
            if bad:
                raise ValueError("statuses that don't fit the rule's kind: " + "; ".join(bad))
        return self

    return model_validator(mode="after")(check)


REQUEST_ANSWERS = ("yes", "no", "none_yet")
RESPONDED_ANSWERS = ("yes", "no")


def route_status(request: str, agent_responded: str) -> str:
    """The route status from the two answers. Exclusion needs a clear 'none_yet' (the user never said what they
    wanted) or a clear 'no' (the agent never answered the request); everything else that came up is applied."""
    if request == "no":
        return "not_applicable"
    if request == "none_yet":
        return "ended_before_start"
    return "applied" if agent_responded == "yes" else "ended_before_finish"


def route_schema(procedures: list[Procedure]) -> type[BaseModel]:
    ids = [p.id for p in procedures]
    decision = create_model(
        "RouteDecision",
        __base__=_Model,
        procedure=(Literal[tuple(ids)], ...),  # type: ignore[valid-type]
        reason=(str, Field(description="One sentence, in English.")),
        request=(Literal[REQUEST_ANSWERS], Field(description="Did the user express the need this procedure covers? yes, no, or none_yet (the conversation ended before the user said what they wanted at all).")),  # type: ignore[valid-type]
        agent_responded=(Literal[RESPONDED_ANSWERS], Field(description="If request is yes: did the agent respond to that request at least once? Otherwise: no.")),  # type: ignore[valid-type]
    )
    return create_model(
        "RouteReply", __base__=_Model, procedures=(list[decision], ...), __validators__={"complete": _complete_list("procedures", "procedure", ids)}
    )


def route_messages(procedures: list[Procedure], variables: dict[str, str], turns: list[dict], tool_calls: list[dict]) -> list[dict[str, Any]]:
    prompt = prompts.load("route")
    listing = "\n\n".join(render_route_procedure(p, variables) for p in procedures)
    return [
        {"role": "system", "content": [_block(prompt.text, cache=True)]},
        {
            "role": "user",
            "content": [
                _block(f"<procedures>\n{listing}\n</procedures>", cache=True),
                _block(render_transcript(turns, tool_calls)),
                _block("For each procedure, by its id, answer both questions for this conversation."),
            ],
        },
    ]


def route(llm: LLM, procedures: list[Procedure], variables: dict[str, str], turns: list[dict], tool_calls: list[dict]) -> list[Routed]:
    if not procedures:
        return []
    schema = route_schema(procedures)
    reply = llm.complete("fast", route_messages(procedures, variables, turns, tool_calls), schema)
    got = {d.procedure: d for d in reply.procedures}
    return [Routed(p.id, route_status(got[p.id].request, got[p.id].agent_responded), got[p.id].reason) for p in procedures]


# --- check -----------------------------------------------------------------------


_KIND_STATUSES = {"step": JUDGED_STEP_STATUSES, "never": NEVER_STATUSES, "warning": WARNING_STATUSES}
ALL_STATUSES = tuple(dict.fromkeys(JUDGED_STEP_STATUSES + NEVER_STATUSES + WARNING_STATUSES))


def check_schema(p: Procedure) -> type[BaseModel]:
    keys = [r.key for r in p.rules]
    judgment = create_model(
        "RuleJudgment",
        __base__=_Model,
        rule=(Literal[tuple(keys)], Field(description="The rule: step_N, never_N or warning_N.")),  # type: ignore[valid-type]
        reason=(str, Field(description="One sentence, in English.")),
        evidence=(list[Evidence], Field(description="Where it happened; may be empty for some statuses.")),
        status=(Literal[ALL_STATUSES], Field(description="Steps: done, skipped, not_reached or not_applicable. Never rules: violated or ok. Warning signs: present, unheeded or absent.")),  # type: ignore[valid-type]
    )
    kinds = {r.key: _KIND_STATUSES[r.kind] for r in p.rules}
    return create_model(
        "CheckReply",
        __base__=_Model,
        rules=(list[judgment], Field(description="One entry per rule, in the procedure's order.")),
        goal_reason=(str, Field(description="One sentence, in English.")),
        goal_reached=(Literal["yes", "no", "unclear"], ...),
        __validators__={"complete": _complete_list("rules", "rule", keys, kinds)},
    )


def check_messages(p: Procedure, variables: dict[str, str], turns: list[dict], tool_calls: list[dict]) -> list[dict[str, Any]]:
    prompt = prompts.load("check")
    return [
        {"role": "system", "content": [_block(prompt.text, cache=True)]},
        {
            "role": "user",
            "content": [
                _block(render_transcript(turns, tool_calls), cache=True),
                _block(render_procedure(p, variables), cache=True),
                _block(f"Check the conversation against the procedure '{p.name}'. Give a status for every rule ({', '.join(r.key for r in p.rules)}) and the goal."),
            ],
        },
    ]


@dataclass
class Checked:
    rules: list[Judged]
    goal_reached: bool | None
    goal_reason: str


def agent_quote(evidence: list[dict[str, Any]], turns: list[dict]) -> bool:
    """Some evidence quotes an agent turn exactly (not an approximate match)."""
    return any(
        not e.get("approximate") and (e.get("quote") or "").strip() and isinstance(e.get("turn"), int) and turns[e["turn"]].get("role") == "agent"
        for e in evidence
    )


def check(llm: LLM, p: Procedure, variables: dict[str, str], turns: list[dict], tool_calls: list[dict]) -> Checked:
    reply = llm.complete("reasoning", check_messages(p, variables, turns, tool_calls), check_schema(p))
    got = {j.rule: j for j in reply.rules}
    rules = []
    for r in p.rules:
        j = got[r.key]
        evidence = verify_evidence([e.model_dump() for e in j.evidence], turns)
        status, reason = j.status, j.reason.strip()
        if status == "violated" and not agent_quote(evidence, turns):
            # a never rule counts as violated only on a quote of the agent's own words
            status, reason, evidence = "ok", f"{reason} (Not counted: no quote of the agent's own words shows it.)".strip(), []
        rules.append(Judged(r.id, r.kind, r.index, status, evidence, reason, r.required))
    goal = {"yes": True, "no": False}.get(reply.goal_reached)
    return Checked(rules, goal, reply.goal_reason.strip())
