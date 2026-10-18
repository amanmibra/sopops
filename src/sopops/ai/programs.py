"""The decisions of routing and of one procedure's check as `dspy.Module`s, so `dspy.experimental.ReAnchor` can fit
their thresholds against a metric on labeled sessions, and `calibration` can store them per workspace.

The programs run the decisions only (no review by the reasoning model, no evidence): what the thresholds decide.
`calibrate` fits one program and saves its thresholds; the pipeline reads them from then on, for every question whose
wording is unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import dspy
from dspy.experimental import ReAnchor
from sqlalchemy.engine import Engine

from ..pipeline.procedures import Procedure
from ..pipeline.rollup import FINDING_STATUSES, Judged, rollup
from . import calibration, decide
from .signatures import RuleChecks


class CheckDecisions(dspy.Module):
    """One procedure's rule decisions over a session: `forward(turns)` gives `result`, `statuses` by rule id, and
    `failing` (the rule ids with a finding)."""

    def __init__(self, p: Procedure, splits: dict[str, RuleChecks], variables: dict[str, str], cfg: decide.DecisionConfig, client: Any):
        super().__init__()
        self.items = decide.items_for(p, splits)
        self.brief = decide._brief(p, variables)
        self.cfg = cfg
        self.decide = decide.decision_predictor(self.items, variables, cfg)
        self.decide.set_lm(client)

    def forward(self, turns: list[dict[str, Any]]) -> dspy.Prediction:
        lines = decide.transcript_lines(turns)
        names = list(self.decide.signature.output_fields)
        preds = [self.decide(procedure=self.brief, transcript=decide.utterances(seg)) for seg in decide.segments(self.brief.model_dump(), lines)]
        got = decide.combine(preds, names)
        decisions = {it.rule.id: decide.decide(it, got, self.cfg) for it in self.items}
        decide.mark_not_reached([decisions[it.rule.id] for it in self.items if it.rule.kind == "step"])
        rules = [Judged(it.rule.id, it.rule.kind, it.rule.index, decisions[it.rule.id].status, [], "", it.rule.required) for it in self.items]
        agent_spoke = any(t.get("role") == "agent" and t.get("text") for t in turns)
        roll = rollup(rules, got["goal"].yes, agent_spoke)
        return dspy.Prediction(result=roll.result, statuses={r.rule_id: r.status for r in rules}, failing={r.rule_id for r in rules if r.status in FINDING_STATUSES})


class RouteDecisions(dspy.Module):
    """Routing over a session: `forward(turns)` gives `applied`, the procedure ids that applied."""

    def __init__(self, procedures: list[Procedure], variables: dict[str, str], cfg: decide.DecisionConfig, client: Any):
        super().__init__()
        self.procedures, self.variables, self.cfg = procedures, variables, cfg
        self.decide = decide.route_predictor(procedures, cfg)
        self.decide.set_lm(client)

    def forward(self, turns: list[dict[str, Any]]) -> dspy.Prediction:
        keys = {p.id: decide.field_name(f"p_{p.id}") for p in self.procedures}
        briefs = {keys[p.id]: decide._brief(p, self.variables) for p in self.procedures}
        lines = decide.transcript_lines(turns)
        if not any(x.speaker == "user" for x in lines):
            return dspy.Prediction(applied=set())
        names = list(self.decide.signature.output_fields)
        got = decide.combine([self.decide(procedures=briefs, transcript=decide.utterances(seg)) for seg in decide.segments(briefs, lines)], names)
        return dspy.Prediction(applied={p.id for p in self.procedures if got[f"{keys[p.id]}_situation"].yes and got[f"{keys[p.id]}_responded"].yes})


Metric = Callable[[dspy.Example, dspy.Prediction], float]


def calibrate(program: CheckDecisions | RouteDecisions, trainset: list[dspy.Example], metric: Metric, engine: Engine, workspace: str, name: str,
              model: str | None, valset: list[dspy.Example] | None = None, num_threads: int = 8) -> dict[str, Any]:
    """Fit the program's thresholds with ReAnchor (5-fold checked) and store them for `workspace` as `name`
    (`route` or `check:<procedure id>`). Returns ReAnchor's report: scores before and after, and what it fitted."""
    optimizer = ReAnchor(metric, num_threads=num_threads)
    fitted = optimizer.compile(program, trainset=trainset, valset=valset)
    params = {n: {"threshold": cfg["threshold"]} for n, cfg in fitted.decide.fields.items() if "threshold" in cfg}
    calibration.save(engine, workspace, name, params, decide.question_hashes(program.decide), model, optimizer.report)
    return optimizer.report
