"""Routing, rule checks and pattern assignment as decisions, and the reasoning model's review of what they flag.

**Decisions** run on the `decide` role: TypeSafe's Jev when it's on, else the fast model (DSPy asks it for probability
evidence and decodes it the same way). Each answer is a probability; a calibrated threshold per question
(`sopops.ai.calibration`, else the defaults in `DecisionConfig`) makes it a yes or no, and a band around it marks it
unsure.

**Rules as literal checks.** Jev reads a question literally, so each rule is first rewritten by the reasoning model
(`SplitRule`) as a condition and positive actions, once per rule version (stored in `rule_checks`, keyed by sopc's rule
id and the text's hash).

**Route.** Per procedure, two questions: did the situation it's for come up, and did the agent act on it. Below the
threshold: not applicable; situation but no response: ended before finishing. When an answer is unsure and review is
on, the session is routed by `fallback` (the LLM) instead.

**Check.** Per rule: a question for its condition (steps and warning signs), and one per action (and per forbidden
action of a warning sign), all in one decision call per transcript segment; then where each action happened (a Choice
over utterance ids on Jev, `LocateEvidence` on a generative model). A step not done after the last done step is
`not_reached`, else `skipped`. A never rule is violated only on an agent utterance. Rules flagged (`review_statuses`)
or unsure go to the reasoning model (`CheckRules`, restricted to those rules) for status and reason; the rest keep the
decision's status and evidence. `mode="review"` has the reasoning model judge every rule (the full check).

**Long transcripts** are cut into overlapping segments whose state fits 28k estimated tokens and 255 utterances (one
evidence Choice covers a segment). A question's probability is the highest across segments (it happened if it happened
in any), and its evidence comes from that segment.

**Assignment.** One Choice per finding over its rule's failure modes, plus `other`.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from typing import Any

import dspy
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from ..orm import RuleChecks as RuleChecksRow
from ..pipeline.procedures import Procedure, Rule, fill
from ..pipeline.rollup import Judged
from ..pipeline.transcript import (
    agent_quote,
    render_procedure,
    render_transcript,
    verify_evidence,
)
from ..pipeline.types import Checked, Routed
from . import calibration
from .lm import AI, DECIDE
from .signatures import (
    VERSIONS,
    CheckRules,
    LocateEvidence,
    ProcedureBrief,
    RuleChecks,
    SplitRule,
    Utterance,
    action_question,
    assign_question,
    assign_signature,
    check_signature,
    condition_question,
    decision_predict,
    evidence_question,
    evidence_signature,
    field_name,
    goal_question,
    route_questions,
    route_signature,
)

log = logging.getLogger("sopops.ai.decide")

MAX_STATE_TOKENS = 28_000  # below Jev's documented 32k (state plus the longest question)
MAX_OPTIONS = 255  # a Choice's limit
OVERLAP = 4  # utterances repeated between segments
QUOTE_WORDS = 25
KIND_STATUSES = {"step": ("done", "skipped", "not_reached", "not_applicable"), "never": ("violated", "ok"), "warning": ("present", "unheeded", "absent")}
EVIDENCE_BATCH = 24  # evidence questions per request


# --- configuration -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Band:
    """How a probability becomes a decision: yes at or above `decide` (the default threshold, unless calibrated),
    unsure strictly between `no` and `yes`."""

    no: float
    yes: float
    decide: float = 0.5

    def unsure(self, a: Answer) -> bool:
        """Unsure: inside the band. When the question's threshold was calibrated away from `decide`, the band also
        covers the band moved with it, so calibration can widen what is reviewed but never narrow it: the band is
        where review pays, and a threshold fitted to the decisions alone says nothing about that."""
        shift = a.t - self.decide
        return min(self.no, self.no + shift) < a.p < max(self.yes, self.yes + shift)


@dataclass(frozen=True)
class DecisionConfig:
    """Defaults, bands and review. `SOPOPS_CHECK_REVIEW=off` lets the decisions stand (no reasoning model);
    `SOPOPS_CHECK_MODE=review` has the reasoning model judge every rule; `SOPOPS_DECISIONS` (JSON) overrides any field,
    bands as `[no, yes, decide]`, e.g. `{"never": [0.05, 0.7, 0.7], "review_statuses": ["violated"]}`."""

    # Tuned on a private benchmark's dev split with review on (and kept on held-out): a wide review band for routing,
    # steps and never rules (a done step under 0.9, a never rule over 0.05 goes to review). Narrower bands review
    # fewer rules and lose recall.
    route: Band = Band(0.2, 0.8, 0.5)
    responded: Band = Band(0.2, 0.8, 0.5)
    condition: Band = Band(0.3, 0.7, 0.5)
    step: Band = Band(0.3, 0.9, 0.5)
    never: Band = Band(0.05, 0.7, 0.7)  # a violation only when confident; unsure ones are reviewed
    warning: Band = Band(0.3, 0.7, 0.5)
    goal: float = 0.5
    review: bool = True
    review_route: bool = True
    review_statuses: tuple[str, ...] = ("skipped", "violated", "unheeded")
    mode: str = "decide"  # decide | review (the reasoning model judges every rule)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DecisionConfig:
        env = os.environ if env is None else env
        over: dict[str, Any] = json.loads(env.get("SOPOPS_DECISIONS") or "{}")
        if (env.get("SOPOPS_CHECK_REVIEW") or "").strip().lower() in ("off", "0", "false", "no"):
            over["review"] = False
        if (env.get("SOPOPS_CHECK_MODE") or "").strip():
            over["mode"] = env["SOPOPS_CHECK_MODE"].strip()
        return cls.parse(over)

    @classmethod
    def parse(cls, over: Mapping[str, Any]) -> DecisionConfig:
        unknown = set(over) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown decision settings: {', '.join(sorted(unknown))}")
        out: dict[str, Any] = {}
        for f in fields(cls):
            if f.name in over:
                v = over[f.name]
                if isinstance(f.default, Band):
                    out[f.name] = Band(*v) if isinstance(v, (list, tuple)) else Band(**v)
                elif f.name == "review_statuses":
                    out[f.name] = tuple(v)
                else:
                    out[f.name] = type(f.default)(v)
        if out.get("mode", "decide") not in ("decide", "review"):
            raise ValueError("mode is decide or review")
        return replace(cls(), **out)

    def as_dict(self) -> dict[str, Any]:
        return {f.name: ([*getattr(self, f.name).__dict__.values()] if isinstance(getattr(self, f.name), Band) else getattr(self, f.name)) for f in fields(self)}


@dataclass
class Stats:
    """What the decisions settled alone, for logs and the benchmark."""

    routes: int = 0
    routes_reviewed: int = 0
    rules: int = 0
    rules_reviewed: int = 0
    assigned: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, **counts: int) -> None:
        with self._lock:
            for k, v in counts.items():
                setattr(self, k, getattr(self, k) + v)

    def as_dict(self) -> dict[str, int]:
        return {k: getattr(self, k) for k in ("routes", "routes_reviewed", "rules", "rules_reviewed", "assigned")}


STATS = Stats()


# --- the transcript as decisions see it ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    turn: int  # index into the session's turns
    id: str
    speaker: str
    text: str


def transcript_lines(turns: list[dict[str, Any]]) -> list[Line]:
    width = max(3, len(str(max(len(turns) - 1, 0))))
    out = []
    for i, t in enumerate(turns):
        text = (t.get("text") or t.get("name") or "").strip()
        if text:
            role = t.get("role") if t.get("role") in ("agent", "user", "tool") else "system"
            out.append(Line(i, f"U{i:0{width}d}", role, text))
    return out


def utterances(lines: list[Line]) -> list[Utterance]:
    return [Utterance(id=x.id, speaker=x.speaker, text=x.text) for x in lines]  # type: ignore[arg-type]


def _tokens(value: Any) -> int:
    """A deliberately high estimate from JSON length (short utterances in JSON run close to 2 characters a token)."""
    return int(len(json.dumps(value, ensure_ascii=False, default=str)) / 2.0) + 1


def segments(header: Any, lines: list[Line], budget: int = MAX_STATE_TOKENS, max_lines: int = MAX_OPTIONS) -> list[list[Line]]:
    """Consecutive, overlapping runs of lines whose state fits `budget` tokens and `max_lines` utterances."""
    base = _tokens(header)
    size_of = {x.id: _tokens({"id": x.id, "speaker": x.speaker, "text": x.text}) for x in lines}
    out: list[list[Line]] = []
    cur: list[Line] = []
    size = base
    for x in lines:
        n = size_of[x.id]
        if base + n > budget:
            raise ValueError(f"utterance {x.id} and the procedure's text exceed the decision state budget")
        if cur and (size + n > budget or len(cur) >= max_lines):
            out.append(cur)
            cur = cur[-OVERLAP:]
            while cur and base + sum(size_of[y.id] for y in cur) + n > budget:
                cur = cur[1:]
            size = base + sum(size_of[y.id] for y in cur)
        cur.append(x)
        size += n
    if cur:
        out.append(cur)
    return out


def procedure_brief(p: Procedure, variables: dict[str, str]) -> ProcedureBrief:
    return ProcedureBrief(name=p.name, goal=fill(p.goal, variables), when=fill(p.when, variables))


def _quote(text: str) -> str:
    return " ".join(text.split()[:QUOTE_WORDS])


@dataclass
class Answer:
    """One question across segments: the highest probability, whether any segment said yes, and that segment."""

    p: float
    yes: bool
    segment: int
    t: float = 0.5  # the question's threshold


def combine(per_segment: list[Any], predictor: dspy.Predict) -> dict[str, Answer]:
    out: dict[str, Answer] = {}
    for i, pred in enumerate(per_segment):
        for n, cfg in predictor.fields.items():
            d = pred[n]
            p = float(d.probability if d.probability is not None else (1.0 if d.value else 0.0))
            prev = out.get(n)
            if prev is None or p > prev.p:
                out[n] = Answer(p, bool(d.value) or (prev.yes if prev else False), i, float(cfg.get("threshold", 0.5)))
            elif d.value:
                prev.yes = True
    return out


# --- route -------------------------------------------------------------------------------------------------------


def route_predictor(procedures: list[Procedure], cfg: DecisionConfig, params: dict[str, dict[str, Any]] | None = None) -> dspy.Predict:
    keys = [field_name(f"p_{p.id}") for p in procedures]
    names = [f"{k}_{q}" for k in keys for q in ("situation", "responded")]
    defaults = {n: {"threshold": (cfg.route if n.endswith("_situation") else cfg.responded).decide} for n in names}
    return decision_predict(route_signature(keys), route_questions(keys), {**defaults, **(params or {})})


@dataclass
class Routing:
    decisions: list[Routed]
    reviewed: bool  # an unsure decision sent it to the reasoning model
    model: str


def route(ai: AI, procedures: list[Procedure], variables: dict[str, str], turns: list[dict], cfg: DecisionConfig,
          params: dict[str, dict[str, Any]] | None = None) -> Routing:
    """Route decisions. When one is unsure (and review is on), the reasoning model answers the same questions."""
    if not procedures:
        return Routing([], False, str(ai.model(DECIDE)))
    keys = {p.id: field_name(f"p_{p.id}") for p in procedures}
    briefs = {keys[p.id]: procedure_brief(p, variables) for p in procedures}
    lines = transcript_lines(turns)
    STATS.add(routes=1)
    if not any(x.speaker == "user" for x in lines):
        return Routing([Routed(p.id, "ended_before_start", "The user never spoke.") for p in procedures], False, str(ai.model(DECIDE)))
    predictor = route_predictor(procedures, cfg, params)

    def run(role: str) -> tuple[list[Routed], bool]:
        got = combine([ai.predict(role, predictor, procedures=briefs, transcript=utterances(seg)) for seg in segments(briefs, lines)], predictor)
        out, unsure = [], False
        for p in procedures:
            k = keys[p.id]
            s, r = got[f"{k}_situation"], got[f"{k}_responded"]
            status = "not_applicable" if not s.yes else "ended_before_finish" if not r.yes else "applied"
            unsure |= cfg.route.unsure(s) or (s.yes and cfg.responded.unsure(r))
            out.append(Routed(p.id, status, f"The situation came up ({s.p:.2f}); the agent acted on it ({r.p:.2f})."))
        return out, unsure

    decisions, unsure = run(DECIDE)
    if unsure and cfg.review and cfg.review_route:
        STATS.add(routes_reviewed=1)
        return Routing(run("reasoning")[0], True, str(ai.model("reasoning")))
    return Routing(decisions, False, str(ai.model(DECIDE)))


def route_params(engine: Engine, workspace: str, procedures: list[Procedure], cfg: DecisionConfig) -> dict[str, dict[str, Any]]:
    """The workspace's calibrated routing thresholds, for questions that haven't changed."""
    return calibration.load(engine, workspace, "route", question_hashes(route_predictor(procedures, cfg)))


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
        condition=((c.condition or "").strip() or None) if rule.kind != "never" else None,
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
            c = tidy(r, ai.predict("reasoning", SplitRule, kind=r.kind, rule=r.text, procedure=context).checks)
            have[(r.id, r.text_hash)] = c
            new.append(RuleChecksRow(workspace_id=workspace, rule_id=r.id, text_hash=r.text_hash, kind=r.kind, condition=c.condition, actions=c.actions,
                                     forbidden=c.forbidden, model=ai.model("reasoning"), created_at=datetime.now(timezone.utc)))
        if new:
            with Session(engine) as db:
                for row in new:
                    db.merge(row)
                db.commit()
    return {r.id: have[(r.id, r.text_hash)] for r in p.rules}


@dataclass
class Item:
    """One rule and the names of its questions."""

    rule: Rule
    checks: RuleChecks
    cond: str | None
    actions: list[str]
    forbidden: list[str]


def items_for(p: Procedure, splits: dict[str, RuleChecks]) -> list[Item]:
    out = []
    for r in p.rules:
        c = splits[r.id]
        base = f"{r.kind}{r.index}"
        out.append(Item(r, c, f"{base}_cond" if c.condition else None, [f"{base}_a{j}" for j in range(len(c.actions))], [f"{base}_f{j}" for j in range(len(c.forbidden))]))
    return out


def check_questions(items: list[Item], variables: dict[str, str]) -> dict[str, dict[str, Any]]:
    qs: dict[str, dict[str, Any]] = {"goal": goal_question()}
    for it in items:
        if it.cond:
            qs[it.cond] = condition_question(fill(it.checks.condition or "", variables))
        for n, a in zip(it.actions, it.checks.actions):
            qs[n] = action_question(fill(a, variables))
        for n, f in zip(it.forbidden, it.checks.forbidden):
            qs[n] = action_question(fill(f, variables))
    return qs


def check_defaults(items: list[Item], cfg: DecisionConfig) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {"goal": {"threshold": cfg.goal}}
    for it in items:
        band = {"step": cfg.step, "never": cfg.never, "warning": cfg.warning}[it.rule.kind]
        if it.cond:
            out[it.cond] = {"threshold": (cfg.warning if it.rule.kind == "warning" else cfg.condition).decide}
        for n in it.actions + it.forbidden:
            out[n] = {"threshold": band.decide}
    return out


def decision_predictor(items: list[Item], variables: dict[str, str], cfg: DecisionConfig, params: dict[str, dict[str, Any]] | None = None) -> dspy.Predict:
    qs = check_questions(items, variables)
    return decision_predict(check_signature(list(qs)), qs, {**check_defaults(items, cfg), **(params or {})})


def question_hashes(predictor: dspy.Predict) -> dict[str, str]:
    return {n: calibration.question_hash({k: v for k, v in cfg.items() if k in ("instructions", "criteria")}) for n, cfg in predictor.fields.items()}


@dataclass
class Decision:
    status: str
    unsure: bool
    evidence: list[dict[str, Any]]
    reason: str
    find: list[str] = field(default_factory=list)  # question names whose evidence to look up


def decide(it: Item, got: dict[str, Answer], cfg: DecisionConfig) -> Decision:
    """The decided status of one rule. A step not done comes back `skipped`; `not_reached` is worked out over the steps."""
    kind = it.rule.kind
    cond = got[it.cond] if it.cond else None
    acts = [got[n] for n in it.actions]
    forb = [got[n] for n in it.forbidden]
    weakest = min(acts, key=lambda a: a.p - a.t, default=Answer(0.0, False, 0))  # every action must happen
    p = weakest.p
    if kind == "step":
        applicable = cond is None or cond.yes
        done = all(a.yes for a in acts) and bool(acts)
        unsure = (cond is not None and cfg.condition.unsure(cond)) or (applicable and cfg.step.unsure(weakest))
        why = f"Decided: condition {cond.p:.2f}; " if cond else "Decided: "
        if not applicable:
            return Decision("not_applicable", unsure, [], f"{why}the step's situation didn't arise.")
        if done:
            return Decision("done", unsure, [], f"{why}done ({p:.2f}).", find=list(it.actions))
        return Decision("skipped", unsure, [], f"{why}not done ({p:.2f}).")
    if kind == "never":
        if all(a.yes for a in acts) and acts:
            return Decision("violated", cfg.never.unsure(weakest), [], f"Decided: the agent did it ({p:.2f}).", find=list(it.actions))
        return Decision("ok", cfg.never.unsure(weakest), [], f"Decided: the agent didn't do it ({p:.2f}).")
    if cond is None:  # a warning sign with no situation to ask about: the reasoning model decides
        return Decision("absent", True, [], "Decided: no situation to ask about.")
    if not cond.yes:
        return Decision("absent", cfg.warning.unsure(cond), [], f"Decided: the situation didn't come up ({cond.p:.2f}).")
    missed = [a for a in acts if not a.yes]
    did = [n for n, f in zip(it.forbidden, forb) if f.yes]
    unsure = cfg.warning.unsure(cond) or any(cfg.warning.unsure(x) for x in acts + forb)
    status = "unheeded" if missed or did else "present"
    tail = "and the agent didn't act on it" if status == "unheeded" else "and was handled"
    return Decision(status, unsure, [], f"Decided: the situation came up ({cond.p:.2f}) {tail}.", find=[it.cond, *did])


def mark_not_reached(steps: list[Decision]) -> None:
    """A step not done after the last step that was done: the conversation ended first."""
    done = [i for i, d in enumerate(steps) if d.status == "done"]
    if not done:
        return
    for i, d in enumerate(steps):
        if i > done[-1] and d.status == "skipped":
            d.status, d.reason = "not_reached", d.reason.replace("not done", "not reached")


def run_decisions(ai: AI, predictor: Any, brief: ProcedureBrief, lines: list[Line]) -> tuple[list[list[Line]], dict[str, Answer]]:
    segs = segments(brief.model_dump(), lines)
    preds = [ai.predict(DECIDE, predictor, procedure=brief, transcript=utterances(seg)) for seg in segs]
    return segs, combine(preds, predictor)


def find_evidence(ai: AI, items: list[Item], decisions: dict[str, Decision], got: dict[str, Answer], segs: list[list[Line]],
                  brief: ProcedureBrief, variables: dict[str, str], turns: list[dict]) -> None:
    """Where each decided action (or warning-sign situation) happened: a Choice over utterance ids per segment on a
    System One backend, else one `LocateEvidence` call on the fast model."""
    texts: dict[str, tuple[str, bool]] = {}  # question name -> (what to find, said by the user)
    for it in items:
        d = decisions[it.rule.id]
        for n in d.find:
            if n == it.cond:
                texts[n] = (fill(it.checks.condition or "", variables), True)
            elif n in it.actions:
                texts[n] = (fill(it.checks.actions[it.actions.index(n)], variables), False)
            else:
                texts[n] = (fill(it.checks.forbidden[it.forbidden.index(n)], variables), False)
    if not texts:
        return
    found: dict[str, Line] = {}
    if ai.decision_native():
        by_segment: dict[tuple[int, bool], list[str]] = {}
        for n, (_, user) in texts.items():
            by_segment.setdefault((got[n].segment, user), []).append(n)
        for (si, user), names in by_segment.items():
            ids = [x.id for x in segs[si] if x.speaker == ("user" if user else "agent")]
            if not ids:
                continue
            by_id = {x.id: x for x in segs[si]}
            for start in range(0, len(names), EVIDENCE_BATCH):
                batch = names[start : start + EVIDENCE_BATCH]
                qs = {f"{n}_ev": evidence_question(texts[n][0], "the user" if user else "the agent") for n in batch}
                pred = ai.predict(DECIDE, decision_predict(evidence_signature(list(qs), ids), qs), procedure=brief, transcript=utterances(segs[si]))
                for n in batch:
                    line = by_id.get(str(pred[f"{n}_ev"].value))
                    if line is not None:
                        found[n] = line
    else:
        keys = {f"e{i}": n for i, n in enumerate(texts)}
        located = ai.predict("fast", LocateEvidence, transcript=render_transcript(turns, []), actions={k: texts[n][0] for k, n in keys.items()}).located
        lines = {x.turn: x for s in segs for x in s}
        for loc in located:
            n = keys.get(loc.key)
            if n is not None and isinstance(loc.turn, int) and loc.turn in lines:
                found[n] = lines[loc.turn]
    for it in items:
        d = decisions[it.rule.id]
        picked = sorted({found[n].turn: found[n] for n in d.find if n in found}.values(), key=lambda x: x.turn)
        if it.rule.kind == "never" and d.status == "violated":
            picked = [x for x in picked if x.speaker == "agent"]
            if not picked:  # a violation counts only on the agent's own words
                d.status, d.unsure, d.reason = "ok", True, f"{d.reason} No agent utterance shows it."
        d.evidence = [{"turn": x.turn, "quote": _quote(x.text), "source": "transcript"} for x in picked]


@dataclass
class Decided:
    checked: Checked
    reviewed: int
    model: str
    prompt_version: str


def check(ai: AI, engine: Engine, workspace: str, p: Procedure, variables: dict[str, str], turns: list[dict], tool_calls: list[dict],
          cfg: DecisionConfig, params: dict[str, dict[str, Any]] | None = None) -> Decided:
    if cfg.mode == "review":
        return _review(ai, p, variables, turns, tool_calls, list(p.rules), {}, None, "")
    items = items_for(p, rule_checks(ai, engine, workspace, p, variables))
    brief = procedure_brief(p, variables)
    predictor = decision_predictor(items, variables, cfg)
    if params is None:
        params = calibration.load(engine, workspace, f"check:{p.id}", question_hashes(predictor))
    predictor = decision_predictor(items, variables, cfg, params)
    segs, got = run_decisions(ai, predictor, brief, transcript_lines(turns))
    decisions = {it.rule.id: decide(it, got, cfg) for it in items}
    mark_not_reached([decisions[it.rule.id] for it in items if it.rule.kind == "step"])
    find_evidence(ai, items, decisions, got, segs, brief, variables, turns)
    goal = got["goal"]
    review = [it.rule for it in items if cfg.review and (decisions[it.rule.id].unsure or decisions[it.rule.id].status in cfg.review_statuses)]
    STATS.add(rules=len(items), rules_reviewed=len(review))
    if review:
        return _review(ai, p, variables, turns, tool_calls, review, decisions, goal.yes, f"Decided: goal reached ({goal.p:.2f}).")
    rules = [Judged(r.id, r.kind, r.index, decisions[r.id].status, decisions[r.id].evidence, decisions[r.id].reason, r.required) for r in p.rules]
    return Decided(Checked(rules, goal.yes, f"Decided: goal reached ({goal.p:.2f})."), 0, str(ai.model(DECIDE)), VERSIONS["decide"])


def _review(ai: AI, p: Procedure, variables: dict[str, str], turns: list[dict], tool_calls: list[dict], review: list[Rule],
            decisions: dict[str, Decision], goal: bool | None, goal_reason: str) -> Decided:
    """The reasoning model judges `review` (every rule in review mode); the other rules keep their decisions."""
    reply = ai.predict("reasoning", CheckRules, transcript=render_transcript(turns, tool_calls), procedure=render_procedure(p, variables),
                       rules_to_check=[r.key for r in review])
    by_key = {j.rule: j for j in reply.rules}
    for r in review:
        j = by_key.get(r.key)
        if j is None or j.status not in KIND_STATUSES[r.kind]:
            if r.id not in decisions:  # review mode: a rule the model left out stays unjudged as its kind's default
                decisions[r.id] = Decision({"step": "not_applicable", "never": "ok", "warning": "absent"}[r.kind], False, [], "Not judged.")
            continue
        ev = verify_evidence([e.model_dump() for e in j.evidence], turns)
        status, reason = j.status, j.reason.strip()
        if status == "violated" and not agent_quote(ev, turns):
            status, reason, ev = "ok", f"{reason} (Not counted: no quote of the agent's own words shows it.)", []
        decisions[r.id] = Decision(status, False, ev, reason)
    rules = [Judged(r.id, r.kind, r.index, decisions[r.id].status, decisions[r.id].evidence, decisions[r.id].reason, r.required) for r in p.rules]
    whole = len(review) == len(p.rules) and not goal_reason
    model = str(ai.model("reasoning")) if whole else f"{ai.model(DECIDE)}+{ai.model('reasoning')}"
    version = VERSIONS["check"] if whole else f"{VERSIONS['decide']}+{VERSIONS['check']}"
    return Decided(Checked(rules, {"yes": True, "no": False}.get(reply.goal_reached), reply.goal_reason.strip()), len(review), model[:256], version[:64])


# --- pattern assignment ------------------------------------------------------------------------------------------


@dataclass
class AssignGroup:
    """One rule's findings to sort: `modes` maps a mode key to its name and description; `findings` a finding key to
    its status, reason and quotes."""

    rule: str
    modes: dict[str, str]
    findings: dict[str, dict[str, Any]]


def assign(ai: AI, groups: list[AssignGroup]) -> dict[str, str]:
    """{finding key: mode key or 'other'}."""
    out: dict[str, str] = {}
    for g in groups:
        keys = list(g.findings)
        qs = {k: assign_question(k) for k in keys}
        pred = ai.predict(DECIDE, decision_predict(assign_signature(keys, g.modes), qs), rule=g.rule, findings=g.findings)
        for k in keys:
            out[k] = str(pred[k].value)
    STATS.add(assigned=len(out))
    return out


