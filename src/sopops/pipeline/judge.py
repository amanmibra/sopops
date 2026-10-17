"""The two model calls: route (fast model) and check (reasoning model, one call per procedure, every rule at once).

Each builds a reply schema for the exact procedures or rules in play (one field per procedure or rule, with only the
statuses that rule kind allows), so a model with native structured output can't leave one out or mix up kinds.

Messages are laid out for prompt caching: the instructions (same for every call), then the transcript, then the
procedure, then the question. Breakpoints after the instructions and the transcript (route: after the procedure list,
which every session in a workspace shares) let the per-procedure checks of one session reuse the cached transcript.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

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


class RouteDecision(_Model):
    reason: str = Field(description="One sentence, in English.")
    status: Literal[ROUTE_STATUSES]  # type: ignore[valid-type]


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


def route_schema(procedures: list[Procedure]) -> type[BaseModel]:
    fields: dict[str, Any] = {
        f"p{i + 1}": (RouteDecision, Field(description=f"Procedure '{p.id}': {p.name}")) for i, p in enumerate(procedures)
    }
    return create_model("RouteReply", __base__=_Model, **fields)


def route_messages(procedures: list[Procedure], variables: dict[str, str], turns: list[dict], tool_calls: list[dict]) -> list[dict[str, Any]]:
    prompt = prompts.load("route")
    listing = "\n\n".join(f"p{i + 1}:\n{render_route_procedure(p, variables)}" for i, p in enumerate(procedures))
    return [
        {"role": "system", "content": [_block(prompt.text, cache=True)]},
        {
            "role": "user",
            "content": [
                _block(f"<procedures>\n{listing}\n</procedures>", cache=True),
                _block(render_transcript(turns, tool_calls)),
                _block("For each procedure (p1, p2, ...), decide its status for this conversation."),
            ],
        },
    ]


def route(llm: LLM, procedures: list[Procedure], variables: dict[str, str], turns: list[dict], tool_calls: list[dict]) -> list[Routed]:
    if not procedures:
        return []
    schema = route_schema(procedures)
    reply = llm.complete("fast", route_messages(procedures, variables, turns, tool_calls), schema)
    out = []
    for i, p in enumerate(procedures):
        d: RouteDecision | None = getattr(reply, f"p{i + 1}", None)
        out.append(Routed(p.id, d.status, d.reason) if d else Routed(p.id, "not_applicable", "No decision returned."))
    return out


# --- check -----------------------------------------------------------------------


_KIND_STATUSES = {"step": JUDGED_STEP_STATUSES, "never": NEVER_STATUSES, "warning": WARNING_STATUSES}
_rule_models: dict[str, type[BaseModel]] = {}


def _rule_model(kind: str) -> type[BaseModel]:
    if kind not in _rule_models:
        _rule_models[kind] = create_model(
            f"{kind.capitalize()}Judgment",
            __base__=_Model,
            reason=(str, Field(description="One sentence, in English.")),
            evidence=(list[Evidence], Field(description="Where it happened; may be empty for some statuses.")),
            status=(Literal[_KIND_STATUSES[kind]], ...),  # type: ignore[valid-type]
        )
    return _rule_models[kind]


def check_schema(p: Procedure) -> type[BaseModel]:
    fields: dict[str, Any] = {r.key: (_rule_model(r.kind), Field(description=r.text[:300])) for r in p.rules}
    fields["goal_reason"] = (str, Field(description="One sentence, in English."))
    fields["goal_reached"] = (Literal["yes", "no", "unclear"], ...)
    return create_model(f"CheckReply_{len(p.rules)}", __base__=_Model, **fields)


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


def check(llm: LLM, p: Procedure, variables: dict[str, str], turns: list[dict], tool_calls: list[dict]) -> Checked:
    reply = llm.complete("reasoning", check_messages(p, variables, turns, tool_calls), check_schema(p))
    rules = []
    for r in p.rules:
        j = getattr(reply, r.key, None)
        if j is None:  # a schema without native enforcement let it through: say so rather than guess
            status = {"step": "not_applicable", "never": "ok", "warning": "absent"}[r.kind]
            rules.append(Judged(r.id, r.kind, r.index, status, [], "No judgment returned.", r.required))
            continue
        evidence = verify_evidence([e.model_dump() for e in j.evidence], turns)
        rules.append(Judged(r.id, r.kind, r.index, j.status, evidence, j.reason.strip(), r.required))
    goal = {"yes": True, "no": False}.get(reply.goal_reached)
    return Checked(rules, goal, reply.goal_reason.strip())
