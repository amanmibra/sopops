"""Calibrating a workspace's check thresholds from its reviewers' labels: `sopops calibrate` and `POST …/calibrate`.

Each procedure with labeled sessions (`Verdicts.calibration_examples`: verdicts and overrides, this workspace's only)
is fitted with `sopops.ai.programs.calibrate` on its `check:<procedure id>` program, against the procedure and
variables of the most recent labeled session's release and agent. A procedure is refused below the fit's minimums:
fewer labeled sessions than `programs.FOLDS` (the held-out check needs them), or no rule with `programs.MIN_POSITIVES`
labeled failures. A dry run fits and reports without saving. Labels follow rule lineage (`sopops.lineage`): a finding
on an earlier wording of a rule counts for its lineage, and labels the rule as the fitted procedure words it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import dspy

from sqlmodel import Session, select

from . import lineage
from .ai import calibration, decide, programs
from .ai import lm as ai_lm
from .ai.lm import DECIDE
from .orm import Finding
from .pipeline.procedures import candidates

if TYPE_CHECKING:
    from .ai.lm import AI
    from .store import Store


class Calibrate:
    def __init__(self, store: Store):
        self.store = store

    def run(self, ws: str, procedure_id: str | None = None, dry_run: bool = False, ai: AI | None = None) -> dict[str, Any]:
        """{workspace, dry_run, procedures: [{procedure_id, program, labels, negative_labels, saved, skipped, score_before,
        score, rules: [{rule_id, lineage_id, kind, index, text, field, before, after, skipped}]}]}. `skipped` is a refusal,
        {code, message, have, need}, or None (`refusal`)."""
        ai = ai or ai_lm.get_ai()
        if procedure_id:
            pids = [procedure_id]
        else:
            with Session(self.store.engine) as db:
                pids = sorted(set(db.exec(select(Finding.procedure_id).where(Finding.workspace_id == ws).distinct())))
        out = []
        for pid in pids:
            examples = self.store.verdicts.calibration_examples(ws, pid)
            if examples or procedure_id:
                out.append(self._one(ws, pid, examples, dry_run, ai))
        return {"workspace": ws, "dry_run": dry_run, "procedures": out}

    def _one(self, ws: str, pid: str, examples: list, dry_run: bool, ai: AI) -> dict[str, Any]:
        name = f"check:{pid}"
        row: dict[str, Any] = {"procedure_id": pid, "program": name, "labels": len(examples), "negative_labels": sum(len(e.wrong) for e in examples),
                               "saved": False, "skipped": None, "score_before": None, "score": None, "rules": []}
        with Session(self.store.engine) as db:
            lins = lineage.lineages(db, ws, pid)
        examples = _relabel(examples, lambda r: lins.get((pid, r), r))  # every label on its lineage
        support = programs.positives(examples)
        if len(examples) < programs.FOLDS:
            row["skipped"] = refusal("too_few_labeled_sessions", f"{len(examples)} sessions have a reviewed finding; calibrating needs {programs.FOLDS}.",
                                     len(examples), programs.FOLDS)
            return row
        if max(support.values(), default=0) < programs.MIN_POSITIVES:
            most = max(support.values(), default=0)
            row["skipped"] = refusal("too_few_failures", f"No rule has {programs.MIN_POSITIVES} judged failures yet (the most is {most}).", most, programs.MIN_POSITIVES)
            return row
        latest = max(examples, key=lambda e: self.store.sessions.load(ws, e.session_id).started_at)
        cand = candidates(self.store, self.store.sessions.load(ws, latest.session_id))
        p = next((x for x in cand.procedures if x.id == pid), None)
        if p is None:
            row["skipped"] = refusal("not_in_release", f"The procedure isn't in release {cand.release}, the one its latest reviewed session ran.")
            return row
        wording = {lins.get((pid, r.id), r.id): r.id for r in p.rules}
        examples = _relabel(examples, lambda r: wording.get(r, r))  # then on the rule as this release words it
        cfg = decide.DecisionConfig.from_env()
        splits = decide.rule_checks(ai, self.store.engine, ws, p, cand.variables)
        program = programs.CheckDecisions(p, splits, cand.variables, cfg, ai.client(DECIDE))  # type: ignore[attr-defined]
        stored = calibration.load(self.store.engine, ws, name, decide.question_hashes(program.decide))
        defaults = {n: float(c["threshold"]) for n, c in program.decide.fields.items() if "threshold" in c}
        report = programs.calibrate(program, examples, programs.check_objective(), self.store.engine, ws, name, ai.model(DECIDE), save=not dry_run)
        row.update(saved=not dry_run, score_before=report["train_score_before"], score=report["train_score"])
        rules = {r.id: r for r in p.rules}
        with Session(self.store.engine) as db:
            now = {lin: lineage.latest(db, ws, pid, lin) for lin in {lins.get((pid, r.id), r.id) for r in p.rules}}
        for r in report["fitted"]:
            before = float(stored.get(r["field"], {}).get("threshold", defaults[r["field"]]))
            after = before if r.get("skipped") else float(r["value"])
            rid = program.rule_of(r["field"])
            rule, lin = rules.get(rid or ""), lins.get((pid, rid or ""), rid)
            text = (now.get(lin or "") or (None, None, rule.text if rule else None))[2]  # the rule as it reads now (its lineage's latest wording)
            row["rules"].append({"rule_id": rid, "lineage_id": lin, "kind": rule.kind if rule else None, "index": rule.index if rule else None, "text": text,
                                 "field": r["field"], "before": before, "after": after, "skipped": _rule_refusal(r)})
        return row


RULE_SKIPS = {
    "too_few_failures": "{have} judged failures on this rule; it needs {need}.",
    "too_alike": "The checker's answers on this question are too alike to fit a threshold.",
    "held_out": "The new value didn't hold up on held-out sessions.",
}


def refusal(code: str, message: str, have: int | None = None, need: int | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "have": have, "need": need}


def _rule_refusal(row: dict[str, Any]) -> dict[str, Any] | None:
    """Why a question kept its threshold, from a `programs.fit` report row."""
    if not row.get("skipped"):
        return None
    code = row.get("skip_code") or "other"
    return refusal(code, RULE_SKIPS.get(code, str(row["skipped"])).format(have=row.get("have"), need=row.get("need")), row.get("have"), row.get("need"))


def _relabel(examples: list[dspy.Example], to: Callable[[str], str]) -> list[dspy.Example]:
    """The examples with their `failing` and `wrong` rule ids mapped."""
    return [dspy.Example(**{**e.toDict(), "failing": {to(r) for r in e.failing}, "wrong": {to(r) for r in e.wrong}}).with_inputs("turns") for e in examples]
