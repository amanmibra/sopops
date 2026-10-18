"""Jev first, the reasoning model where Jev flags something or isn't sure: routing, checks and pattern assignment.

**Rules as literal checks.** Jev reads a question literally, so each rule is first rewritten by the reasoning model
(`SplitRule`) as a condition and positive actions; that runs once per rule version and is stored (`rule_checks`,
keyed by sopc's rule id and the text's hash). Every question names the part of the state it judges by path.

**Route.** Per procedure, two Nouls over the transcript: did the situation the procedure is for come up, and did the
agent respond to it. Below `route.decide`: not applicable; situation but no response: ended before finishing. When
any answer falls in a band and escalation is on, the session is routed by the LLM instead.

**Check.** Per rule: a Noul for its condition (steps and warning signs), a Noul per action, and an evidence Choice
over the agent's utterance ids per action (the user's for a warning sign's situation), all in one request per
transcript segment. Statuses come from bands (`JevConfig`): a probability at or above `decide` is yes; between `no`
and `yes` is unsure. A step not done after the last done step is `not_reached`, else `skipped` (as DeepTrust's check
does). A never rule is violated only on an agent utterance. Rules Jev flags (`escalate_statuses`) or is unsure about go
to the reasoning model (`CheckRules`: the check prompt restricted to those rules) for status and reason; the rest
keep Jev's status, with its evidence. With escalation off, Jev decides every rule (unsure ones at `decide`).

**Long transcripts** are cut into overlapping segments whose state fits 28k estimated tokens and 255 utterances (so
one evidence Choice covers a segment; no two-pass search). Questions over 60k per request go in further requests over
the same state. A rule's probabilities are the highest across segments (it happened if it happened in any), and each
action's evidence comes from the segment where its answer was highest. A request Jev rejects as too large is re-cut
once at half the budget.

**Pattern assignment.** One Choice per finding over its rule's failure modes, plus `other`.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from ..models import RuleChecks as RuleChecksRow
from ..pipeline.judge import Checked, Routed, agent_quote
from ..pipeline.procedures import Procedure, Rule, fill
from ..pipeline.rollup import Judged
from ..pipeline.transcript import render_procedure, render_transcript, verify_evidence
from .jev import Answers, Choice, Jev, Noul, Question, TooLarge, question_tokens, tokens
from .lm import AI
from .signatures import CHECK_RULES_VERSION, CheckRules, RuleChecks, SplitRule

log = logging.getLogger("sopops.ai.classify")

PROMPT_VERSION = "jev-check-1"
ROUTE_VERSION = "jev-route-1"
MAX_STATE_TOKENS = 28_000  # below the documented 32k (state plus the longest question)
MAX_REQUEST_TOKENS = 60_000  # below the documented 64k
MAX_OPTIONS = 255  # a Choice's limit
OVERLAP = 4  # utterances repeated between segments
QUOTE_WORDS = 25
KIND_STATUSES = {"step": ("done", "skipped", "not_reached", "not_applicable"), "never": ("violated", "ok"), "warning": ("present", "unheeded", "absent")}


# --- configuration -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Band:
    """A Noul probability as a decision: yes at or above `decide`; unsure strictly between `no` and `yes`."""

    no: float
    yes: float
    decide: float = 0.5

    def is_yes(self, p: float) -> bool:
        return p >= self.decide

    def unsure(self, p: float) -> bool:
        return self.no < p < self.yes


@dataclass(frozen=True)
class JevConfig:
    """Thresholds and escalation. `SOPOPS_JEV_ESCALATE=off` turns escalation off; `SOPOPS_JEV_CONFIG` (JSON) overrides
    any field, bands as `[no, yes, decide]`, e.g. `{"never": [0.15, 0.7, 0.7], "escalate_statuses": ["violated"]}`."""

    route: Band = Band(0.2, 0.8, 0.5)
    responded: Band = Band(0.2, 0.8, 0.5)
    condition: Band = Band(0.3, 0.7, 0.5)
    step: Band = Band(0.3, 0.7, 0.5)
    never: Band = Band(0.15, 0.7, 0.7)  # a violation only when Jev is confident; unsure ones are reviewed
    warning: Band = Band(0.3, 0.7, 0.5)
    goal: float = 0.5
    escalate: bool = True
    escalate_route: bool = True
    escalate_statuses: tuple[str, ...] = ("skipped", "violated", "unheeded")
    assign_min: float = 0.0  # a top failure mode below this probability is `other`

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> JevConfig:
        env = os.environ if env is None else env
        over: dict[str, Any] = json.loads(env.get("SOPOPS_JEV_CONFIG") or "{}")
        if (env.get("SOPOPS_JEV_ESCALATE") or "").strip().lower() in ("off", "0", "false", "no"):
            over["escalate"] = False
        return cls.parse(over)

    @classmethod
    def parse(cls, over: Mapping[str, Any]) -> JevConfig:
        out: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in over:
                continue
            v = over[f.name]
            if isinstance(f.default, Band):
                out[f.name] = Band(*v) if isinstance(v, (list, tuple)) else Band(**v)
            elif f.name == "escalate_statuses":
                out[f.name] = tuple(v)
            else:
                out[f.name] = type(f.default)(v)
        unknown = set(over) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown Jev settings: {', '.join(sorted(unknown))}")
        return replace(cls(), **out)

    def as_dict(self) -> dict[str, Any]:
        return {f.name: ([*getattr(self, f.name).__dict__.values()] if isinstance(getattr(self, f.name), Band) else getattr(self, f.name)) for f in fields(self)}


@dataclass
class Stats:
    """What Jev decided alone, for logs and the benchmark."""

    routes: int = 0
    routes_escalated: int = 0
    rules: int = 0
    rules_escalated: int = 0
    assigned: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, **counts: int) -> None:
        with self._lock:
            for k, v in counts.items():
                setattr(self, k, getattr(self, k) + v)

    def as_dict(self) -> dict[str, int]:
        return {k: getattr(self, k) for k in ("routes", "routes_escalated", "rules", "rules_escalated", "assigned")}


STATS = Stats()


# --- the state Jev reads ---------------------------------------------------------------------------------------

SPEAKERS = {
    "agent": "the AI agent the procedure is for",
    "user": "the person the agent is talking to",
    "tool": "a tool the agent called",
    "system": "a system message",
}
AGENT = 'the agent (the AI agent handling the conversation; its utterances have speaker "agent")'
USER = 'the user (the person talking to the agent; their utterances have speaker "user")'


@dataclass(frozen=True)
class Line:
    turn: int  # index into the session's turns
    id: str
    speaker: str
    text: str


def transcript_lines(turns: list[dict[str, Any]]) -> list[Line]:
    width = max(3, len(str(max(len(turns) - 1, 0))))
    return [
        Line(i, f"U{i:0{width}d}", t.get("role") or "system", (t.get("text") or t.get("name") or "").strip())
        for i, t in enumerate(turns)
        if (t.get("text") or t.get("name") or "").strip()
    ]


def build_state(header: dict[str, Any], lines: list[Line], tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    state = {
        **header,
        "transcript": {"speakers": SPEAKERS, "utterances": [{"id": x.id, "speaker": x.speaker, "text": x.text} for x in lines]},
    }
    if tool_calls:
        state["tool_calls"] = [{k: c.get(k) for k in ("name", "arguments", "result", "is_error") if c.get(k) is not None} for c in tool_calls]
    return state


def _line_tokens(x: Line) -> int:
    return tokens({"id": x.id, "speaker": x.speaker, "text": x.text})


def segments(header: dict[str, Any], lines: list[Line], budget: int = MAX_STATE_TOKENS, max_lines: int = MAX_OPTIONS) -> list[list[Line]]:
    """Consecutive, overlapping runs of lines whose state fits `budget` tokens and `max_lines` utterances."""
    base = tokens(build_state(header, []))
    out: list[list[Line]] = []
    cur: list[Line] = []
    size = base
    for x in lines:
        n = _line_tokens(x)
        if base + n > budget:
            raise ValueError(f"utterance {x.id} and the procedure's text exceed the Jev state budget")
        if cur and (size + n > budget or len(cur) >= max_lines):
            out.append(cur)
            cur = cur[-OVERLAP:]
            while cur and base + sum(map(_line_tokens, cur)) + n > budget:
                cur = cur[1:]
            size = base + sum(map(_line_tokens, cur))
        cur.append(x)
        size += n
    if cur:
        out.append(cur)
    return out


def ask_all(jev: Jev, state: dict[str, Any], questions: dict[str, Question], budget: int = MAX_REQUEST_TOKENS) -> Answers:
    """Every question over one state, in as many requests as the request budget needs."""
    room = budget - tokens(state)
    batches: list[dict[str, Question]] = []
    cur: dict[str, Question] = {}
    used = 0
    for k, q in questions.items():
        n = question_tokens(q)
        if cur and used + n > room:
            batches.append(cur)
            cur, used = {}, 0
        cur[k] = q
        used += n
    if cur:
        batches.append(cur)
    out = Answers()
    for b in batches:
        out.update(jev.ask(state, b))
    return out


Build = Callable[[list[Line]], dict[str, Question]]


def over_segments(jev: Jev, header: dict[str, Any], lines: list[Line], build: Build, tool_calls: list[dict[str, Any]] | None = None, scale: float = 1.0) -> list[tuple[list[Line], Answers]]:
    """Each segment's lines and answers; a segment Jev rejects as too large is re-cut once at half the budget."""
    out = []
    for seg in segments(header, lines, int(MAX_STATE_TOKENS * scale)):
        try:
            out.append((seg, ask_all(jev, build_state(header, seg, tool_calls), build(seg), int(MAX_REQUEST_TOKENS * scale))))
        except TooLarge:
            if scale < 1.0:
                raise
            log.warning("Jev rejected a request as too large; re-cutting %d utterances at half the budget", len(seg))
            out += over_segments(jev, header, seg, build, tool_calls, scale / 2)
    return out


def _procedure_header(p: Procedure, variables: dict[str, str]) -> dict[str, Any]:
    return {"name": p.name, "goal": fill(p.goal, variables), "when": fill(p.when, variables)}


def _quote(text: str) -> str:
    words = text.split()
    return " ".join(words[:QUOTE_WORDS])


# --- route -------------------------------------------------------------------------------------------------------


def situation_question(key: str) -> Noul:
    return Noul(
        instructions={
            "question": f"In `transcript.utterances`, did the user ask for, or start, what the procedure `procedures.{key}` is for?",
            "procedure": f"`procedures.{key}.when` says when it applies; `procedures.{key}.goal` is what it achieves.",
        },
        true="At least one utterance shows the user asking for or starting what the procedure covers, even if they only got partway, changed their mind, or it turned out to be unavailable.",
        false="The conversation is about something else, or ends before the user says what they want.",
    )


def responded_question(key: str) -> Noul:
    return Noul(
        instructions={
            "question": f"In `transcript.utterances`, did the agent respond at least once to the user's request for what `procedures.{key}` is for (confirm it, ask about it, answer it, or act on it)?",
            "agent": AGENT,
        },
        true="At least one agent utterance responds to that request.",
        false="The user never made that request, or the conversation ends before the agent replies to it.",
    )


def route(jev: Jev, procedures: list[Procedure], variables: dict[str, str], turns: list[dict], tool_calls: list[dict], cfg: JevConfig,
          fallback: Callable[[], list[Routed]] | None = None) -> tuple[list[Routed], bool]:
    """Route decisions, and whether they came from `fallback` (the LLM) because Jev was unsure."""
    if not procedures:
        return [], False
    keys = {p.id: f"p{i}" for i, p in enumerate(procedures)}
    header = {"procedures": {keys[p.id]: _procedure_header(p, variables) for p in procedures}}
    lines = transcript_lines(turns)
    STATS.add(routes=1)
    if not any(x.speaker == "user" for x in lines):
        return [Routed(p.id, "ended_before_start", "The user never spoke.") for p in procedures], False

    def build(_: list[Line]) -> dict[str, Question]:
        qs: dict[str, Question] = {}
        for k in keys.values():
            qs[f"{k}_situation"] = situation_question(k)
            qs[f"{k}_responded"] = responded_question(k)
        return qs

    got = over_segments(jev, header, lines, build, tool_calls)
    out, unsure = [], False
    for p in procedures:
        k = keys[p.id]
        s = max(a.nouls[f"{k}_situation"] for _, a in got)
        r = max(a.nouls[f"{k}_responded"] for _, a in got)
        if not cfg.route.is_yes(s):
            status = "not_applicable"
        elif not cfg.responded.is_yes(r):
            status = "ended_before_finish"
        else:
            status = "applied"
        unsure |= cfg.route.unsure(s) or (cfg.route.is_yes(s) and cfg.responded.unsure(r))
        out.append(Routed(p.id, status, f"Jev: the situation came up ({s:.2f}); the agent responded ({r:.2f})."))
    if unsure and cfg.escalate and cfg.escalate_route and fallback is not None:
        STATS.add(routes_escalated=1)
        return fallback(), True
    return out, False


# --- rule checks (split once per rule version) -------------------------------------------------------------------

_split_locks: dict[tuple[str, str], threading.Lock] = {}
_split_guard = threading.Lock()


def _split_lock(ws: str, procedure: str) -> threading.Lock:
    with _split_guard:
        return _split_locks.setdefault((ws, procedure), threading.Lock())


def tidy(rule: Rule, c: RuleChecks) -> RuleChecks:
    """What each kind can use: no condition on a never rule, `forbidden` only on warning signs, at least one action
    on steps and never rules (the rule's own text)."""
    actions = [a.strip() for a in c.actions if a and a.strip()]
    if rule.kind in ("step", "never") and not actions:
        actions = [rule.text]
    return RuleChecks(
        condition=(c.condition or "").strip() or None if rule.kind != "never" else None,
        actions=actions,
        forbidden=[f.strip() for f in c.forbidden if f and f.strip()] if rule.kind == "warning" else [],
    )


def rule_checks(ai: AI, engine: Engine, workspace: str, p: Procedure, variables: dict[str, str]) -> dict[str, RuleChecks]:
    """Each rule's literal checks, by rule id: stored ones, and a split (stored) for every rule version not seen yet."""
    with _split_lock(workspace, p.id):
        with Session(engine) as db:
            rows = db.exec(select(RuleChecksRow).where(RuleChecksRow.workspace_id == workspace, RuleChecksRow.rule_id.in_([r.id for r in p.rules]))).all()
            have = {(r.rule_id, r.text_hash): RuleChecks(condition=r.condition, actions=list(r.actions), forbidden=list(r.forbidden)) for r in rows}
        context = render_procedure(p, variables, with_rule_keys=False)
        new = []
        for r in p.rules:
            if (r.id, r.text_hash) in have:
                continue
            pred = ai.predict("reasoning", SplitRule, kind=r.kind, rule=r.text, procedure=context)
            c = tidy(r, pred.checks)
            have[(r.id, r.text_hash)] = c
            new.append(RuleChecksRow(workspace_id=workspace, rule_id=r.id, text_hash=r.text_hash, kind=r.kind, condition=c.condition, actions=c.actions,
                                     forbidden=c.forbidden, model=ai.model("reasoning"), created_at=datetime.now(timezone.utc)))
        if new:
            with Session(engine) as db:
                for row in new:
                    db.merge(row)
                db.commit()
    return {r.id: have[(r.id, r.text_hash)] for r in p.rules}


# --- check -------------------------------------------------------------------------------------------------------


def action_question(action: str, actor: str = AGENT) -> Noul:
    return Noul(
        instructions={"question": f"In `transcript.utterances`, did {actor} do what `action` describes?", "action": action, "procedure": "`procedure` is the procedure the agent follows, for context."},
        true=f"At least one utterance shows {actor.split(' (')[0]} doing this.",
        false=f"No utterance shows {actor.split(' (')[0]} doing this.",
    )


def condition_question(situation: str) -> Noul:
    return Noul(
        instructions={"question": "In `transcript.utterances`, did the situation in `situation` arise?", "situation": situation},
        true="At least one utterance shows this situation arising.",
        false="No utterance shows this situation.",
    )


def evidence_question(action: str, ids: list[str], actor: str = AGENT) -> Choice:
    return Choice(
        instructions={"question": f"Which utterance in `transcript.utterances` is where {actor.split(' (')[0]} does what `action` describes?", "action": action},
        options={i: None for i in ids},
    )


def situation_evidence_question(situation: str, ids: list[str]) -> Choice:
    return Choice(
        instructions={"question": "Which utterance in `transcript.utterances` shows the situation in `situation` arising?", "situation": situation},
        options={i: None for i in ids},
    )


def goal_question() -> Noul:
    return Noul(
        instructions={"question": "In `transcript.utterances`, by the end of the conversation, was the outcome in `procedure.goal` achieved?", "agent": AGENT},
        true="The utterances show the outcome reached, for example confirmed by the agent or the user.",
        false="The conversation ends without the utterances showing the outcome reached, or they show it abandoned or refused.",
    )


@dataclass
class _Rule:
    rule: Rule
    key: str
    checks: RuleChecks


def _questions(items: list[_Rule], variables: dict[str, str], seg: list[Line]) -> dict[str, Question]:
    agent_ids = [x.id for x in seg if x.speaker == "agent"]
    user_ids = [x.id for x in seg if x.speaker == "user"]
    qs: dict[str, Question] = {"goal": goal_question()}
    for it in items:
        c, k = it.checks, it.key
        if c.condition:
            qs[f"{k}_cond"] = condition_question(fill(c.condition, variables))
            if it.rule.kind == "warning" and user_ids:
                qs[f"{k}_cond_ev"] = situation_evidence_question(fill(c.condition, variables), user_ids)
        for j, a in enumerate(c.actions):
            a = fill(a, variables)
            qs[f"{k}_a{j}"] = action_question(a)
            if agent_ids:
                qs[f"{k}_a{j}_ev"] = evidence_question(a, agent_ids)
        for j, f in enumerate(c.forbidden):
            f = fill(f, variables)
            qs[f"{k}_f{j}"] = action_question(f)
            if agent_ids:
                qs[f"{k}_f{j}_ev"] = evidence_question(f, agent_ids)
    return qs


@dataclass
class _Combined:
    """One question's answer across segments: the highest probability, and the evidence line from that segment."""

    p: float
    line: Line | None


def _combine(got: list[tuple[list[Line], Answers]], key: str, ev_key: str | None) -> _Combined | None:
    best: _Combined | None = None
    for seg, a in got:
        if key not in a.nouls:
            continue
        line = None
        if ev_key and a.ranked(ev_key):
            by_id = {x.id: x for x in seg}
            line = by_id.get(a.ranked(ev_key)[0][0])
        if best is None or a.nouls[key] > best.p:
            best = _Combined(a.nouls[key], line)
    return best


def _evidence(lines: list[Line | None]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for x in sorted((x for x in lines if x is not None), key=lambda x: x.turn):
        if x.turn not in seen:
            seen.add(x.turn)
            out.append({"turn": x.turn, "quote": _quote(x.text), "source": "transcript"})
    return out


@dataclass
class Decision:
    status: str
    unsure: bool
    evidence: list[dict[str, Any]]
    reason: str


def decide(it: _Rule, got: list[tuple[list[Line], Answers]], cfg: JevConfig) -> Decision:
    """Jev's status for one rule. Steps not done come back `skipped`; `not_reached` is worked out over the steps."""
    k, kind, c = it.key, it.rule.kind, it.checks
    cond = _combine(got, f"{k}_cond", f"{k}_cond_ev") if c.condition else None
    acts = [_combine(got, f"{k}_a{j}", f"{k}_a{j}_ev") for j in range(len(c.actions))]
    forb = [_combine(got, f"{k}_f{j}", f"{k}_f{j}_ev") for j in range(len(c.forbidden))]
    acts_ = [a for a in acts if a is not None]
    forb_ = [f for f in forb if f is not None]
    if kind == "step":
        applicable = cond is None or cfg.condition.is_yes(cond.p)
        p = min((a.p for a in acts_), default=0.0)
        unsure = (cond is not None and cfg.condition.unsure(cond.p)) or (applicable and cfg.step.unsure(p))
        why = f"Jev: condition {cond.p:.2f}; " if cond else "Jev: "
        if not applicable:
            return Decision("not_applicable", unsure, [], f"{why}the step's situation didn't arise.")
        if cfg.step.is_yes(p):
            return Decision("done", unsure, _evidence([a.line for a in acts_]), f"{why}done ({p:.2f}).")
        return Decision("skipped", unsure, [], f"{why}not done ({p:.2f}).")
    if kind == "never":
        p = min((a.p for a in acts_), default=0.0)
        ev = _evidence([a.line for a in acts_])
        if cfg.never.is_yes(p):
            if ev:  # evidence is only ever searched among the agent's utterances
                return Decision("violated", cfg.never.unsure(p), ev, f"Jev: the agent did it ({p:.2f}).")
            return Decision("ok", True, [], f"Jev: it happened ({p:.2f}), but no agent utterance shows it.")
        return Decision("ok", cfg.never.unsure(p), [], f"Jev: the agent didn't do it ({p:.2f}).")
    # warning sign
    if cond is None:  # a warning sign with no situation to ask about: the reasoning model decides
        return Decision("absent", True, [], "Jev: no situation to ask about.")
    if not cfg.warning.is_yes(cond.p):
        return Decision("absent", cfg.warning.unsure(cond.p), [], f"Jev: the situation didn't come up ({cond.p:.2f}).")
    missed = [a for a in acts_ if not cfg.warning.is_yes(a.p)]
    did = [f for f in forb_ if cfg.warning.is_yes(f.p)]
    unsure = cfg.warning.unsure(cond.p) or any(cfg.warning.unsure(x.p) for x in acts_ + forb_)
    ev = _evidence([cond.line] + [f.line for f in did])
    if missed or did:
        return Decision("unheeded", unsure, ev, f"Jev: the situation came up ({cond.p:.2f}) and the agent didn't act on it.")
    return Decision("present", unsure, ev, f"Jev: the situation came up ({cond.p:.2f}) and was handled.")


def mark_not_reached(steps: list[tuple[_Rule, Decision]]) -> None:
    """A step not done after the last step that was done: the conversation ended first."""
    done = [i for i, (_, d) in enumerate(steps) if d.status == "done"]
    if not done:
        return
    for i, (_, d) in enumerate(steps):
        if i > done[-1] and d.status == "skipped":
            d.status, d.reason = "not_reached", d.reason.replace("not done", "not reached")


@dataclass
class JevChecked:
    checked: Checked
    escalated: int
    model: str
    prompt_version: str


def check(jev: Jev, ai: AI, engine: Engine, workspace: str, p: Procedure, variables: dict[str, str], turns: list[dict], tool_calls: list[dict], cfg: JevConfig) -> JevChecked:
    splits = rule_checks(ai, engine, workspace, p, variables)
    items = [_Rule(r, f"r{i}", splits[r.id]) for i, r in enumerate(p.rules)]
    header = {"procedure": _procedure_header(p, variables)}
    got = over_segments(jev, header, transcript_lines(turns), lambda seg: _questions(items, variables, seg), tool_calls)
    decisions = {it.rule.id: decide(it, got, cfg) for it in items}
    mark_not_reached([(it, decisions[it.rule.id]) for it in items if it.rule.kind == "step"])
    goal_p = max(a.nouls.get("goal", 0.0) for _, a in got)
    goal: bool | None = goal_p >= cfg.goal
    goal_reason = f"Jev: goal reached ({goal_p:.2f})."

    escalate = [it.rule for it in items if cfg.escalate and (decisions[it.rule.id].unsure or decisions[it.rule.id].status in cfg.escalate_statuses)]
    STATS.add(rules=len(items), rules_escalated=len(escalate))
    model, version = jev.model, PROMPT_VERSION
    if escalate:
        reply = ai.predict(
            "reasoning", CheckRules,
            transcript=render_transcript(turns, tool_calls), procedure=render_procedure(p, variables), rules_to_check=[r.key for r in escalate],
        )
        by_key = {j.rule: j for j in reply.rules}
        for r in escalate:
            j = by_key.get(r.key)
            if j is None or j.status not in KIND_STATUSES[r.kind]:
                continue  # missing or doesn't fit the rule's kind: Jev's status stands
            ev = verify_evidence([e.model_dump() for e in j.evidence], turns)
            status, reason = j.status, j.reason.strip()
            if status == "violated" and not agent_quote(ev, turns):
                status, reason, ev = "ok", f"{reason} (Not counted: no quote of the agent's own words shows it.)", []
            decisions[r.id] = Decision(status, False, ev, reason)
        goal = {"yes": True, "no": False}.get(reply.goal_reached)
        goal_reason = reply.goal_reason.strip()
        model, version = f"{jev.model}+{ai.model('reasoning')}", f"{PROMPT_VERSION}+{CHECK_RULES_VERSION}"
    rules = [Judged(r.id, r.kind, r.index, decisions[r.id].status, decisions[r.id].evidence, decisions[r.id].reason, r.required) for r in p.rules]
    return JevChecked(Checked(rules, goal, goal_reason), len(escalate), model[:256], version[:64])


# --- pattern assignment ------------------------------------------------------------------------------------------


@dataclass
class AssignGroup:
    """One rule's findings to sort: `modes` maps a mode key to its name and description; `findings` a finding key to
    its status, reason and quotes."""

    rule: str
    modes: dict[str, str]
    findings: dict[str, dict[str, Any]]


def assign(jev: Jev, groups: list[AssignGroup], cfg: JevConfig) -> dict[str, str]:
    """{finding key: mode key or 'other'}."""
    out: dict[str, str] = {}
    for g in groups:
        state = {"rule": g.rule, "findings": g.findings}
        options: dict[str, str | None] = {**g.modes, "other": "None of these failure modes describes what the agent did wrong."}
        qs: dict[str, Question] = {
            k: Choice(
                instructions={"question": f"Which failure mode describes what the agent did wrong in `findings.{k}` (how it broke `rule`)? Match the mechanism of the failure, not the topic of the conversation."},
                options=options,
            )
            for k in g.findings
        }
        got = ask_all(jev, state, qs)
        for k in g.findings:
            ranked = got.ranked(k)
            out[k] = ranked[0][0] if ranked and ranked[0][1] >= cfg.assign_min else "other"
    STATS.add(assigned=len(out))
    return out
