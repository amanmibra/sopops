"""Storing and reading SOP check results: procedure_checks, rule_results and findings.

Every write checks the session's revision (its payload hash) first, inside the transaction: a run queued before the
session was re-sent writes nothing. Re-running a revision replaces its rows, so the pipeline is idempotent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete
from sqlmodel import Session, select

from .models import Finding, ProcedureCheck, RuleResult, SessionRecord
from .pipeline.models import SessionRef
from .pipeline.rollup import FINDING_STATUSES, Judged, Rollup, lenient, worst

if TYPE_CHECKING:
    from .pipeline.judge import Routed
    from .pipeline.procedures import Procedure
    from .store import Store


class Checks:
    def __init__(self, store: Store):
        self.store = store

    @property
    def sessions(self):
        return self.store.sessions

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    def _current(self, db: Session, ref: SessionRef) -> SessionRecord | None:
        q = select(SessionRecord).where(SessionRecord.workspace_id == ref.workspace_id, SessionRecord.id == ref.session_id)
        row = db.exec(q.with_for_update() if self.sessions.postgres else q).first()
        return row if row is not None and row.payload_hash == ref.revision else None

    def _clear(self, db: Session, session_id: str, procedure_id: str | None = None) -> None:
        for table in (Finding, RuleResult) + (() if procedure_id else (ProcedureCheck,)):
            stmt = delete(table).where(table.session_id == session_id)
            if procedure_id:
                stmt = stmt.where(table.procedure_id == procedure_id)
            db.execute(stmt)

    def _set(self, db: Session, row: SessionRecord, status: str, reason: str | None, result: str | None) -> None:
        row.status, row.status_reason, row.result = status, reason, result
        row.processed_at = row.updated_at = _now()
        db.add(row)
        db.flush()
        self.sessions._emit(db, {"type": "updated", "workspace": row.workspace_id, "id": row.id, "status": status})

    # --- writes ------------------------------------------------------------------

    def reset(self, ref: SessionRef, status: str, reason: str | None) -> bool:
        """Drop any results and set the status (no model configured, nothing to check against)."""
        with self._db() as db, db.begin():
            row = self._current(db, ref)
            if row is None:
                return False
            self._clear(db, row.id)
            self._set(db, row, status, reason, None)
            return True

    def routed(self, ref: SessionRef, procedures: list[Procedure], decisions: list[Routed], status: str, reason: str | None, model: str | None, prompt_version: str) -> bool:
        """Replace the session's results with the router's decisions: one procedure_check per candidate procedure."""
        by_id = {p.id: p for p in procedures}
        now = _now()
        with self._db() as db, db.begin():
            row = self._current(db, ref)
            if row is None:
                return False
            self._clear(db, row.id)
            for d in decisions:
                p = by_id[d.procedure_id]
                db.add(
                    ProcedureCheck(
                        id=str(uuid.uuid4()), workspace_id=row.workspace_id, session_id=row.id, procedure_id=p.id, procedure_name=p.name,
                        procedure_version=p.version, release_id=p.release, applicable=d.applicable, excluded_reason=d.excluded_reason,
                        route_reason=d.reason, model=model, prompt_version=prompt_version, revision=ref.revision, checked_at=now,
                    )
                )
            self._set(db, row, status, reason, None)
            return True

    def save(self, ref: SessionRef, p: Procedure, rules: list[Judged], goal_reached: bool | None, goal_reason: str, roll: Rollup, model: str | None, prompt_version: str) -> bool:
        """One procedure's check: its rule results, findings and rolled-up result."""
        texts = {r.id: r for r in p.rules}
        now = _now()
        with self._db() as db, db.begin():
            row = self._current(db, ref)
            if row is None:
                return False
            self._clear(db, row.id, p.id)
            findings: list[Finding] = []
            for j in rules:
                rule = texts[j.rule_id]
                reason = j.reason
                if lenient(j, goal_reached):
                    reason = f"{reason} (Lenient: the goal was reached and this step isn't required, so it doesn't make the procedure deviated.)".strip()
                rr = RuleResult(
                    id=str(uuid.uuid4()), workspace_id=row.workspace_id, session_id=row.id, procedure_id=p.id, rule_id=j.rule_id,
                    kind=j.kind, index=j.index, rule_text=rule.text, rule_text_hash=rule.text_hash, status=j.status, evidence=j.evidence, reason=reason,
                )
                db.add(rr)
                if j.status in FINDING_STATUSES:
                    findings.append(Finding(id=str(uuid.uuid4()), workspace_id=row.workspace_id, session_id=row.id, procedure_id=p.id, rule_id=j.rule_id,
                                            rule_result_id=rr.id, status=j.status, created_at=now))
            db.flush()  # rule results first: findings reference them (no ORM relationship orders the inserts)
            db.add_all(findings)
            pc = db.exec(select(ProcedureCheck).where(ProcedureCheck.session_id == row.id, ProcedureCheck.procedure_id == p.id)).first()
            if pc is None:
                pc = ProcedureCheck(id=str(uuid.uuid4()), workspace_id=row.workspace_id, session_id=row.id, procedure_id=p.id, procedure_name=p.name,
                                    procedure_version=p.version, release_id=p.release, applicable=True, revision=ref.revision, checked_at=now)
            pc.result, pc.goal_reached, pc.goal_reason = roll.result, goal_reached, goal_reason
            pc.steps_applicable, pc.steps_done, pc.nevers_violated = roll.steps_applicable, roll.steps_done, roll.nevers_violated
            pc.model, pc.prompt_version, pc.checked_at = model, prompt_version, now
            db.add(pc)
            return True

    def finish(self, ref: SessionRef, keep_partial: bool) -> tuple[str, str | None] | None:
        """Every check is in: the session is checked (or stays partial), with the worst procedure result."""
        with self._db() as db, db.begin():
            row = self._current(db, ref)
            if row is None:
                return None
            results = list(db.exec(select(ProcedureCheck.result).where(ProcedureCheck.session_id == row.id, ProcedureCheck.result.is_not(None))))
            result = worst(results)
            status = "partial" if keep_partial else "checked"
            self._set(db, row, status, row.status_reason if keep_partial else None, result)
            return status, result

    def fail(self, ref: SessionRef, reason: str) -> None:
        with self._db() as db, db.begin():
            row = self._current(db, ref)
            if row is not None:
                self._set(db, row, "failed", reason[:2000], None)

    # --- reads -----------------------------------------------------------------------

    def for_session(self, session_id: str) -> list[dict[str, Any]]:
        """Each procedure the router considered, with its rule results (in procedure order: steps, nevers, warnings)."""
        order = {"step": 0, "never": 1, "warning": 2}
        with self._db() as db:
            checks = list(db.exec(select(ProcedureCheck).where(ProcedureCheck.session_id == session_id)))
            rules = list(db.exec(select(RuleResult).where(RuleResult.session_id == session_id)))
        by_proc: dict[str, list[RuleResult]] = {}
        for r in rules:
            by_proc.setdefault(r.procedure_id, []).append(r)
        out = []
        for c in checks:
            rr = sorted(by_proc.get(c.procedure_id, []), key=lambda r: (order.get(r.kind, 9), r.index))
            out.append({
                "procedure_id": c.procedure_id, "procedure_name": c.procedure_name, "procedure_version": c.procedure_version,
                "release_id": c.release_id, "applicable": c.applicable, "excluded_reason": c.excluded_reason, "route_reason": c.route_reason,
                "result": c.result, "goal_reached": c.goal_reached, "goal_reason": c.goal_reason, "steps_applicable": c.steps_applicable,
                "steps_done": c.steps_done, "nevers_violated": c.nevers_violated, "model": c.model, "prompt_version": c.prompt_version,
                "checked_at": _utc(c.checked_at),
                "rules": [
                    {"rule_id": r.rule_id, "kind": r.kind, "index": r.index, "text": r.rule_text, "status": r.status, "evidence": r.evidence,
                     "reason": r.reason, "finding": r.status in FINDING_STATUSES}
                    for r in rr
                ],
            })
        rank = {"deviated": 0, "partial": 1, "followed": 2, "not_measurable": 3}
        out.sort(key=lambda c: (not c["applicable"], c["excluded_reason"] is not None, rank.get(c["result"] or "", 4), c["procedure_name"] or ""))
        return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime) -> datetime:
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
