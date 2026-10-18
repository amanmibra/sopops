"""Calibrating a workspace's check thresholds from its reviewers' labels: `sopops calibrate` and `POST …/calibrate`.

Each procedure with labeled sessions (`Verdicts.calibration_examples`: verdicts and overrides, this workspace's only)
is fitted with `sopops.ai.programs.calibrate` on its `check:<procedure id>` program, against the procedure and
variables of the most recent labeled session's release and agent. A procedure is refused below the fit's minimums:
fewer labeled sessions than `programs.FOLDS` (the held-out check needs them), or no rule with `programs.MIN_POSITIVES`
labeled failures. A dry run fits and reports without saving.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

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
        score, rules: [{rule_id, field, before, after, skipped}]}]}"""
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
        support = programs.positives(examples)
        if len(examples) < programs.FOLDS:
            row["skipped"] = f"{len(examples)} labeled sessions (< {programs.FOLDS})"
            return row
        if max(support.values(), default=0) < programs.MIN_POSITIVES:
            row["skipped"] = f"no rule has {programs.MIN_POSITIVES} labeled failures"
            return row
        latest = max(examples, key=lambda e: self.store.sessions.load(ws, e.session_id).started_at)
        cand = candidates(self.store, self.store.sessions.load(ws, latest.session_id))
        p = next((x for x in cand.procedures if x.id == pid), None)
        if p is None:
            row["skipped"] = f"the procedure isn't in release {cand.release}"
            return row
        cfg = decide.DecisionConfig.from_env()
        splits = decide.rule_checks(ai, self.store.engine, ws, p, cand.variables)
        program = programs.CheckDecisions(p, splits, cand.variables, cfg, ai.client(DECIDE))  # type: ignore[attr-defined]
        stored = calibration.load(self.store.engine, ws, name, decide.question_hashes(program.decide))
        defaults = {n: float(c["threshold"]) for n, c in program.decide.fields.items() if "threshold" in c}
        report = programs.calibrate(program, examples, programs.check_objective(), self.store.engine, ws, name, ai.model(DECIDE), save=not dry_run)
        row.update(saved=not dry_run, score_before=report["train_score_before"], score=report["train_score"])
        for r in report["fitted"]:
            before = float(stored.get(r["field"], {}).get("threshold", defaults[r["field"]]))
            after = before if r.get("skipped") else float(r["value"])
            row["rules"].append({"rule_id": program.rule_of(r["field"]), "field": r["field"], "before": before, "after": after, "skipped": r.get("skipped")})
        return row
