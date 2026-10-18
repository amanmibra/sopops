"""The decisions of routing and of one procedure's check as `dspy.Module`s, and `calibrate`, which fits their
thresholds to a workspace's labeled sessions and stores them (`calibration`); the pipeline reads them from then on,
for every question whose wording is unchanged. Labeled sessions come from reviewers' verdicts and overrides
(`sopops.verdicts.Verdicts.calibration_examples`). Two generative programs: `SuggestEdits`, a procedure edit proposed
by the reasoning model and checked, with one retry; and `NamePattern`, a rule pattern's short name (with `rule_phrase`,
the rule's own text trimmed, when there's no model or its answer won't do).

The programs run the decisions only (no review by the reasoning model, no evidence): what the thresholds decide.

**How a fit works.** A threshold only changes how a probability is read, never the request, so each training session
is answered once (`answers`) and every candidate setting is judged offline from those probabilities (`judge`): a fit
costs one decision call per session and segment, on Jev or on a generative model alike, and the search takes seconds.

**What it optimizes** is a corpus-level objective over all the training sessions, not a per-session mean: for checks,
`check_objective` (needs-attention precision at a fixed recall, and per-rule F1), for routing `route_objective`.

**Regularized**, because a few dozen labeled sessions can't support extreme settings: thresholds stay within
`bounds`; a question is fitted only when its rule has at least `min_positives` labeled failures (the goal question
and routing use every session); a change is kept only when it beats the current setting on held-out folds; and among
equal scores the setting nearest the default wins.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import dspy
from sqlalchemy.engine import Engine

from ..pipeline.procedures import Procedure
from ..pipeline.rollup import FINDING_STATUSES, Judged, rollup
from . import calibration, decide
from .lm import AI
from .signatures import ExampleIn, NameRulePattern, RuleChecks, SuggestEdit

FOLDS = 5
MAX_CANDIDATES = 40
BOUNDS = (0.1, 0.9)
MIN_POSITIVES = 3
SWEEPS = 2


def _with_thresholds(got: dict[str, decide.Answer], thresholds: dict[str, float]) -> dict[str, decide.Answer]:
    """The answers read at `thresholds`: yes when the highest probability across segments reaches it."""
    out = {}
    for n, a in got.items():
        t = thresholds.get(n, a.t)
        out[n] = decide.Answer(a.p, a.p >= t, a.segment, t)
    return out


class CheckDecisions(dspy.Module):
    """One procedure's rule decisions over a session: `forward(turns)` gives `result`, `statuses` by rule id, and
    `failing` (the rule ids with a finding)."""

    def __init__(self, p: Procedure, splits: dict[str, RuleChecks], variables: dict[str, str], cfg: decide.DecisionConfig, client: Any):
        super().__init__()
        self.items = decide.items_for(p, splits)
        self.brief = decide.procedure_brief(p, variables)
        self.cfg = cfg
        self.decide = decide.decision_predictor(self.items, variables, cfg)
        self.decide.set_lm(client)

    def rule_of(self, question: str) -> str | None:
        """The rule id a question belongs to (None for the goal)."""
        return next((it.rule.id for it in self.items if question == it.cond or question in it.actions or question in it.forbidden), None)

    def answers(self, turns: list[dict[str, Any]]) -> dict[str, decide.Answer]:
        lines = decide.transcript_lines(turns)
        preds = [self.decide(procedure=self.brief, transcript=decide.utterances(seg)) for seg in decide.segments(self.brief.model_dump(), lines)]
        return decide.combine(preds, self.decide)

    def judge(self, got: dict[str, decide.Answer], agent_spoke: bool, thresholds: dict[str, float] | None = None) -> dspy.Prediction:
        got = _with_thresholds(got, thresholds or {})
        decisions = {it.rule.id: decide.decide(it, got, self.cfg) for it in self.items}
        decide.mark_not_reached([decisions[it.rule.id] for it in self.items if it.rule.kind == "step"])
        rules = [Judged(it.rule.id, it.rule.kind, it.rule.index, decisions[it.rule.id].status, [], "", it.rule.required) for it in self.items]
        roll = rollup(rules, got["goal"].yes, agent_spoke)
        return dspy.Prediction(result=roll.result, statuses={r.rule_id: r.status for r in rules}, failing={r.rule_id for r in rules if r.status in FINDING_STATUSES})

    def forward(self, turns: list[dict[str, Any]]) -> dspy.Prediction:
        return self.judge(self.answers(turns), _agent_spoke(turns))


class RouteDecisions(dspy.Module):
    """Routing over a session: `forward(turns)` gives `applied`, the procedure ids that applied."""

    def __init__(self, procedures: list[Procedure], variables: dict[str, str], cfg: decide.DecisionConfig, client: Any):
        super().__init__()
        self.procedures, self.variables, self.cfg = procedures, variables, cfg
        self.keys = {p.id: decide.field_name(f"p_{p.id}") for p in procedures}
        self.decide = decide.route_predictor(procedures, cfg)
        self.decide.set_lm(client)

    def rule_of(self, question: str) -> str | None:
        return None

    def answers(self, turns: list[dict[str, Any]]) -> dict[str, decide.Answer] | None:
        briefs = {self.keys[p.id]: decide.procedure_brief(p, self.variables) for p in self.procedures}
        lines = decide.transcript_lines(turns)
        if not any(x.speaker == "user" for x in lines):
            return None
        return decide.combine([self.decide(procedures=briefs, transcript=decide.utterances(seg)) for seg in decide.segments(briefs, lines)], self.decide)

    def judge(self, got: dict[str, decide.Answer] | None, agent_spoke: bool = True, thresholds: dict[str, float] | None = None) -> dspy.Prediction:
        if got is None:
            return dspy.Prediction(applied=set())
        got = _with_thresholds(got, thresholds or {})
        return dspy.Prediction(applied={p.id for p in self.procedures if got[f"{self.keys[p.id]}_situation"].yes and got[f"{self.keys[p.id]}_responded"].yes})

    def forward(self, turns: list[dict[str, Any]]) -> dspy.Prediction:
        return self.judge(self.answers(turns))


class SuggestEdits(dspy.Module):
    """A procedure edit from the reasoning model (`SuggestEdit`), checked by `validate(block_id, new_text)`, which
    returns an error message or None. A proposal that fails is sent back once with the error. `forward` gives
    `block_id`, `new_text`, `rationale`, `attempts`, and `error` (None when the last proposal passed)."""

    def __init__(self, ai: AI, validate: Callable[[str, str], str | None], retries: int = 1):
        super().__init__()
        self.ai, self.validate, self.retries = ai, validate, retries

    def forward(self, procedure_id: str, procedure_file: str, rule: str, pattern: str, verdict: str, note: str, examples: list[ExampleIn]) -> dspy.Prediction:
        text, error, reply = "", "", None
        for attempt in range(1, self.retries + 2):
            reply = self.ai.predict("reasoning", SuggestEdit, procedure_id=procedure_id, procedure_file=procedure_file, rule=rule, pattern=pattern,
                                    verdict=verdict, note=note, examples=examples, previous_text=text, previous_error=error)
            text = reply.new_text
            error = self.validate(reply.block_id.strip(), text) or ""
            if not error:
                break
        assert reply is not None
        return dspy.Prediction(block_id=reply.block_id.strip(), new_text=text, rationale=reply.rationale.strip(), attempts=attempt, error=error or None)


NAME_WORDS = 8
NAME_CHARS = 60


def rule_phrase(text: str, words: int = NAME_WORDS, chars: int = NAME_CHARS) -> str:
    """A rule's text as a short name, without a model: its first clause (up to a sentence end, a semicolon, a dash or a
    parenthesis), at most `words` words and `chars` characters (cut at a word, with an ellipsis), no closing period."""
    t = " ".join(re.sub(r"\{\{\s*(\w+)\s*\}\}", r"\1", text or "").split())
    t = re.split(r"(?<=[.!?])\s|;|\s[-–—]\s|\s\(", t, maxsplit=1)[0].strip().rstrip(".:,!?").strip()
    cut = t.split()
    out = " ".join(cut[:words])
    if len(out) > chars:
        out = out[:chars].rsplit(" ", 1)[0]
    if out != t:
        out = out.rstrip(".,;:") + "…"
    return (out[:1].upper() + out[1:]) or "Rule"


class NamePattern(dspy.Module):
    """A rule pattern's name from the reasoning model (`NameRulePattern`): what the agent did wrong in a few plain
    words. A reply that's empty, too long or a rule number falls back to `rule_phrase`. `forward` gives `name` and
    `model` (None for the fallback)."""

    def __init__(self, ai: AI):
        super().__init__()
        self.ai = ai

    def forward(self, kind: str, rule: str, procedure: str) -> dspy.Prediction:
        reply = self.ai.predict("reasoning", NameRulePattern, kind=kind, rule=rule, procedure=procedure)
        name = " ".join(str(reply.name or "").split()).rstrip(".").strip()
        if len(name) > 1 and name[0] in "\"'“" and name[-1] in "\"'”":  # the whole name in quotes
            name = name[1:-1].strip()
        if not name or len(name) > NAME_CHARS or len(name.split()) > NAME_WORDS or re.match(r"(?i)^(step|never rule|warning sign|rule) \d+\b", name):
            return dspy.Prediction(name=rule_phrase(rule), model=None)
        return dspy.Prediction(name=name[:1].upper() + name[1:], model=self.ai.model("reasoning"))


def _agent_spoke(turns: list[dict[str, Any]]) -> bool:
    return any(t.get("role") == "agent" and t.get("text") for t in turns)


# --- objectives --------------------------------------------------------------------------------------------------

Objective = Callable[[Sequence[dspy.Example], Sequence[dspy.Prediction]], float]


def _f1(tp: int, fp: int, fn: int) -> float:
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def check_objective(recall_floor: float = 0.85, rule_weight: float = 0.5, min_positives: int = MIN_POSITIVES) -> Objective:
    """For labeled check sessions (`deviated`: whether it needs attention; `failing`: the rule ids at fault):
    `1 - rule_weight` × needs-attention precision at `recall_floor` (precision, less twice any recall shortfall) plus
    `rule_weight` × the mean F1 of the rules with at least `min_positives` labeled failures."""

    def parts(examples: Sequence[dspy.Example], preds: Sequence[dspy.Prediction]) -> dict[str, float]:
        tp = sum(1 for e, p in zip(examples, preds) if e.deviated and p.result == "deviated")
        fp = sum(1 for e, p in zip(examples, preds) if not e.deviated and p.result == "deviated")
        pos = sum(1 for e in examples if e.deviated)
        counts: dict[str, list[int]] = {}
        for e, p in zip(examples, preds):
            for r in set(e.failing) | set(p.failing):
                c = counts.setdefault(r, [0, 0, 0, 0])  # tp, fp, fn, labeled
                c[0 if r in e.failing and r in p.failing else 1 if r in p.failing else 2] += 1
                c[3] += r in e.failing
        rules = [_f1(c[0], c[1], c[2]) for c in counts.values() if c[3] >= min_positives]
        return {"precision": tp / (tp + fp) if tp + fp else 0.0, "recall": tp / pos if pos else 1.0, "rule_f1": sum(rules) / len(rules) if rules else 0.0}

    def objective(examples: Sequence[dspy.Example], preds: Sequence[dspy.Prediction]) -> float:
        x = parts(examples, preds)
        session = x["precision"] - 2 * max(0.0, recall_floor - x["recall"])
        return (1 - rule_weight) * session + rule_weight * x["rule_f1"]

    objective.parts = parts  # type: ignore[attr-defined]
    return objective


def route_objective(procedure: str) -> Objective:
    """For labeled sessions (`in_scope`): the share routed correctly to `procedure`."""

    def objective(examples: Sequence[dspy.Example], preds: Sequence[dspy.Prediction]) -> float:
        return sum((procedure in p.applied) == bool(e.in_scope) for e, p in zip(examples, preds)) / max(len(examples), 1)

    return objective


def positives(examples: Sequence[dspy.Example]) -> dict[str, int]:
    """Labeled failures per rule id."""
    out: dict[str, int] = {}
    for e in examples:
        for r in getattr(e, "failing", None) or ():
            out[r] = out.get(r, 0) + 1
    return out


# --- the fit -----------------------------------------------------------------------------------------------------


@dataclass
class _Answered:
    example: dspy.Example
    got: Any
    agent_spoke: bool


def _folds(n: int) -> list[list[int]]:
    order = list(range(n))
    random.Random(0).shuffle(order)
    k = min(FOLDS, n)
    return [order[i::k] for i in range(k)]


def _candidates(values: list[float], lo: float, hi: float) -> list[float]:
    """Midpoints between neighbouring observed probabilities, within [lo, hi], plus the bounds."""
    inner = sorted({v for v in values if lo < v < hi})
    if len(inner) > MAX_CANDIDATES - 1:
        step = (len(inner) - 1) / (MAX_CANDIDATES - 2)
        inner = sorted({inner[round(i * step)] for i in range(MAX_CANDIDATES - 1)})
    points = [lo, *inner, hi]
    return sorted({lo, hi, *(round((a + b) / 2, 4) for a, b in zip(points, points[1:]))})


def fit(program: CheckDecisions | RouteDecisions, answered: list[_Answered], objective: Objective, *, bounds: tuple[float, float] = BOUNDS,
        min_positives: int = MIN_POSITIVES) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Coordinate ascent over the program's question thresholds against `objective`, offline. Returns the thresholds
    and one report row per question."""
    lo, hi = bounds
    defaults = {n: float(cfg["threshold"]) for n, cfg in program.decide.fields.items() if "threshold" in cfg}
    current = dict(defaults)
    examples = [a.example for a in answered]
    support = positives(examples)
    observed = {n: [a.got[n].p for a in answered if a.got is not None and n in a.got] for n in defaults}
    folds = _folds(len(answered))

    def score(thresholds: dict[str, float], rows: Sequence[int]) -> float:
        preds = [program.judge(answered[i].got, answered[i].agent_spoke, thresholds) for i in rows]
        return objective([examples[i] for i in rows], preds)

    every = range(len(answered))
    report: dict[str, dict[str, Any]] = {}
    for _ in range(SWEEPS):
        changed = False
        for n in defaults:
            rule = program.rule_of(n)
            if rule is not None and support.get(rule, 0) < min_positives:
                report[n] = {"field": n, "value": defaults[n], "skipped": f"{support.get(rule, 0)} labeled failures on its rule (< {min_positives})"}
                continue
            if len(set(observed[n])) < 2:
                report[n] = {"field": n, "value": current[n], "skipped": "too few distinct probabilities"}
                continue
            tried = []
            for t in _candidates(observed[n], lo, hi):
                th = {**current, n: t}
                tried.append((t, th))

            def best(rows: Sequence[int]) -> tuple[float, dict[str, float], float]:
                scored = [(score(th, rows), -abs(t - defaults[n]), t, th) for t, th in tried]
                s, _, t, th = max(scored, key=lambda x: (x[0], x[1]))
                return t, th, s

            start = score(current, every)
            t, th, s = best(every)
            row = {"field": n, "value": current[n], "score_before": round(start, 4), "observed": len(set(observed[n]))}
            if s > start + 1e-9 and t != current[n]:
                held, held_start = 0.0, 0.0
                for fold in folds:
                    rest = [i for i in every if i not in set(fold)]
                    _, pick, _ = best(rest)
                    held += score(pick, fold)
                    held_start += score(current, fold)
                if held > held_start + 1e-9:
                    current, changed = th, True
                    row.update(value=t, score=round(s, 4))
                else:
                    row.update(skipped=f"fold check: held-out {held:.3f} vs {held_start:.3f}")
            report[n] = row
        if not changed:
            break
    return current, list(report.values())


def calibrate(program: CheckDecisions | RouteDecisions, trainset: list[dspy.Example], objective: Objective, engine: Engine, workspace: str, name: str,
              model: str | None, *, num_threads: int = 8, bounds: tuple[float, float] = BOUNDS, min_positives: int = MIN_POSITIVES, save: bool = True) -> dict[str, Any]:
    """Answer each training session once, fit the program's thresholds against `objective` (see the module docstring)
    and store them for `workspace` as `name` (`route` or `check:<procedure id>`; not with `save=False`, a dry run).
    Returns the report: the objective before and after, and per question what was fitted or why not. Only thresholds
    that moved are stored."""
    if not trainset:
        raise ValueError("trainset must contain at least one example")

    def one(e: dspy.Example) -> _Answered:
        return _Answered(e, program.answers(e.turns), _agent_spoke(e.turns))

    with ThreadPoolExecutor(max(1, num_threads)) as pool:
        answered = list(pool.map(one, trainset))
    defaults = {n: float(cfg["threshold"]) for n, cfg in program.decide.fields.items() if "threshold" in cfg}
    every = list(range(len(answered)))

    def total(th: dict[str, float]) -> float:
        return objective([a.example for a in answered], [program.judge(answered[i].got, answered[i].agent_spoke, th) for i in every])

    before = total(defaults)
    fitted, rows = fit(program, answered, objective, bounds=bounds, min_positives=min_positives)
    report = {"train_score_before": round(before, 4), "train_score": round(total(fitted), 4), "examples": len(trainset), "bounds": list(bounds),
              "min_positives": min_positives, "fitted": rows}
    parts = getattr(objective, "parts", None)
    if parts is not None:
        examples = [a.example for a in answered]
        for key, th in (("parts_before", defaults), ("parts", fitted)):
            report[key] = {k: round(v, 4) for k, v in parts(examples, [program.judge(a.got, a.agent_spoke, th) for a in answered]).items()}
    params = {n: {"threshold": t} for n, t in fitted.items() if not math.isclose(t, defaults[n])}
    for n, t in fitted.items():
        program.decide.fields[n] = {**program.decide.fields[n], "threshold": t}
    if save:
        calibration.save(engine, workspace, name, params, decide.question_hashes(program.decide), model, report)
    return report
