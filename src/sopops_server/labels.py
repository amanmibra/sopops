"""Reviewer labels on single rules: corrections and spot checks (`rule_labels`).

A **correction** gives any rule of a checked session its right status, from the session page: a wrong status, or a
mistake the checker missed (a `done` step that was skipped, an `ok` never rule that was violated). A **spot check**
asks a reviewer about a rule result with no finding, sampled from the checked sessions (`spot_checks`); its answer is
a label too, with `source` `spot_check`. Both are kept by session, procedure and rule, so they outlive a re-check;
the latest call on a rule wins, whether it's a label or an override (`sopops_server.verdicts`).

**What a label changes.** On a rule with a finding, a label is a call on that finding, like an override: a status
that isn't a finding status (`FINDING_STATUSES`) says the finding is wrong, so it's suppressed and left out of
Insights, as it is for an override; a finding status says it's right. A label never adds a finding: a mistake the
checker missed is stored and shown on the session (and is a calibration label), but the session's result, its
patterns and Insights stay what the checker found, so a correction never moves an Insights number unseen.

**Spot checks.** A share of rule results with no finding (the workspace's `spot_check_rate`, default
`SPOT_CHECK_RATE`) is asked about, weighted toward close calls: a decision whose probability was near its threshold,
and a rule where the reasoning model's review changed the decision (`weight`). Which results are sampled is a stable
hash of session, procedure and rule, so the queue doesn't reshuffle between visits, and the weights are scaled by
their mean over the results scanned, so the share asked about stays near the rate.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from .errors import InvalidValue, NotFound
from .orm import Finding, FindingOverride, ProcedureCheck, RuleLabel, RuleResult, SessionRecord, Workspace
from .pipeline.procedures import agent_variables, fill
from .pipeline.rollup import FINDING_STATUSES, NEVER_STATUSES, WARNING_STATUSES

if TYPE_CHECKING:
    from .store import Store

STATUSES = {"step": ("done", "out_of_order", "skipped", "not_reached", "not_applicable"), "never": NEVER_STATUSES, "warning": WARNING_STATUSES}
SOURCES = ("correction", "spot_check")
SPOT_CHECK_RATE = 0.02
SCAN = 5000  # the most recent rule results a spot-check queue samples from
CLOSE_BAND = 0.25  # a probability within this of its threshold is a close call, the closer the heavier
CLOSE_WEIGHT = 4.0
CHANGED_WEIGHT = 4.0  # the review changed the decision
LENIENT_MARK = " (Lenient:"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime | None) -> datetime | None:
    if at is None:
        return None
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)


def check_status(kind: str, status: str) -> None:
    if status not in STATUSES.get(kind, ()):
        raise InvalidValue(f"a {kind} rule's status is one of {', '.join(STATUSES.get(kind, ()))}, not '{status}'")


@dataclass
class Call:
    """A reviewer's latest call on one rule, from a label or an override. `correct`: whether the checker's finding was
    right (for a label, whether its status is a finding status); `status`: the right status, when one was given."""

    correct: bool
    status: str | None
    source: str  # correction | spot_check | override
    checked_status: str | None
    note: str | None
    reviewer: str | None
    created_at: datetime


def call_of(row: FindingOverride | RuleLabel) -> Call:
    if isinstance(row, RuleLabel):
        return Call(row.status in FINDING_STATUSES, row.status, row.source, row.checked_status, row.note, row.reviewer, _utc(row.created_at))  # type: ignore[arg-type]
    return Call(row.correct, row.status, "override", None, row.note, row.reviewer, _utc(row.created_at))  # type: ignore[arg-type]


def latest_calls(db: Session, session_ids: Iterable[str], procedure_id: str | None = None) -> dict[tuple[str, str, str], Call]:
    """The latest call per (session, procedure, rule): a label or an override, whichever came last."""
    ids = list(set(session_ids))
    if not ids:
        return {}
    rows: list[FindingOverride | RuleLabel] = []
    for table in (FindingOverride, RuleLabel):
        q = select(table).where(table.session_id.in_(ids))
        rows += list(db.exec(q.where(table.procedure_id == procedure_id) if procedure_id else q))
    rows.sort(key=lambda r: _utc(r.created_at))  # type: ignore[arg-type,return-value]
    return {(r.session_id, r.procedure_id, r.rule_id): call_of(r) for r in rows}


def correction_out(c: Call | None) -> dict[str, Any] | None:
    """What the session page shows of a call: only one that gave a status."""
    if c is None or c.status is None:
        return None
    return {"status": c.status, "checked_status": c.checked_status, "source": c.source, "note": c.note, "reviewer": c.reviewer, "created_at": c.created_at}


def label_out(row: RuleLabel, suppressed: bool | None = None) -> dict[str, Any]:
    return {"label_id": row.id, "session_id": row.session_id, "procedure_id": row.procedure_id, "rule_id": row.rule_id, "kind": row.kind,
            "checked_status": row.checked_status, "status": row.status, "source": row.source, "note": row.note, "reviewer": row.reviewer,
            "created_at": _utc(row.created_at), "missed": row.status in FINDING_STATUSES and row.checked_status not in FINDING_STATUSES,
            "suppressed": suppressed}


def weight(r: RuleResult) -> float:
    """How much more likely a rule result is to be spot-checked than a plain one (1): more the closer its decision was
    to its threshold, and more again when the review changed the decision."""
    w = 1.0
    if r.decision_p is not None and r.decision_threshold is not None:
        w += CLOSE_WEIGHT * max(0.0, 1.0 - abs(r.decision_p - r.decision_threshold) / CLOSE_BAND)
    if r.reviewed and r.decided_status and r.decided_status != r.status:
        w += CHANGED_WEIGHT
    return w


def why(r: RuleResult) -> list[str]:
    out = []
    if r.decision_p is not None and r.decision_threshold is not None and abs(r.decision_p - r.decision_threshold) < CLOSE_BAND:
        out.append("close_call")
    if r.reviewed and r.decided_status and r.decided_status != r.status:
        out.append("review_changed")
    return out or ["sample"]


def draw(session_id: str, procedure_id: str, rule_id: str) -> float:
    """A stable number in [0, 1) for a rule of a session."""
    digest = hashlib.sha256(f"{session_id}:{procedure_id}:{rule_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


class Labels:
    def __init__(self, store: Store):
        self.store = store

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    # --- writes ----------------------------------------------------------------------------------------------------

    def label(self, ws: str, session_id: str, procedure_id: str, rule_id: str, status: str, note: str | None = None, reviewer: str | None = None,
              source: str = "correction") -> dict[str, Any]:
        """Give one rule of a checked session its right status, then bring its finding (if it has one) in line."""
        if source not in SOURCES:
            raise InvalidValue(f"source is one of {', '.join(SOURCES)}")
        with self._db() as db, db.begin():
            r = db.exec(select(RuleResult).where(RuleResult.workspace_id == ws, RuleResult.session_id == session_id, RuleResult.procedure_id == procedure_id,
                                                 RuleResult.rule_id == rule_id)).first()
            if r is None:
                raise NotFound(f"no checked rule '{rule_id}' of procedure '{procedure_id}' on session '{session_id}'")
            check_status(r.kind, status)
            row = RuleLabel(id=str(uuid.uuid4()), workspace_id=ws, session_id=session_id, procedure_id=procedure_id, rule_id=rule_id, rule_result_id=r.id,
                            kind=r.kind, checked_status=r.status, status=status, source=source, note=note or None, reviewer=reviewer or None, created_at=_now())
            db.add(row)
        self.store.verdicts.refresh(ws, sessions=[session_id])
        with self._db() as db:
            f = db.exec(select(Finding).where(Finding.session_id == session_id, Finding.procedure_id == procedure_id, Finding.rule_id == rule_id)).first()
        return label_out(row, f.suppressed if f else None)

    def set_rate(self, ws: str, rate: float | None) -> dict[str, Any]:
        """The workspace's spot-check rate (None: back to the default)."""
        if rate is not None and not 0.0 <= rate <= 1.0:
            raise InvalidValue("rate is between 0 and 1")
        with self._db() as db, db.begin():
            w = db.get(Workspace, ws)
            if w is None:
                raise NotFound(f"no workspace '{ws}'")
            w.spot_check_rate = rate
            db.add(w)
        return self.settings(ws)

    # --- reads -----------------------------------------------------------------------------------------------------

    def settings(self, ws: str) -> dict[str, Any]:
        with self._db() as db:
            w = db.get(Workspace, ws)
        own = w.spot_check_rate if w else None
        return {"rate": SPOT_CHECK_RATE if own is None else own, "default": own is None}

    def for_session(self, session_id: str) -> dict[tuple[str, str], Call]:
        """{(procedure id, rule id): the latest call} for one session."""
        with self._db() as db:
            return {(pid, rid): c for (_, pid, rid), c in latest_calls(db, [session_id]).items()}

    def spot_checks(self, ws: str, limit: int = 50) -> dict[str, Any]:
        """The spot-check queue: sampled rule results with no finding and no label yet, close calls first (see the
        module docstring). `answered`: sampled ones that have a label."""
        rate = self.settings(ws)["rate"]
        R, S, PC = RuleResult, SessionRecord, ProcedureCheck
        with self._db() as db:
            rows = db.exec(
                select(R, S.started_at, S.agent_id, PC.procedure_name, PC.release_id)
                .join(S, S.id == R.session_id)
                .join(PC, (PC.session_id == R.session_id) & (PC.procedure_id == R.procedure_id))
                .where(R.workspace_id == ws, R.status.not_in(FINDING_STATUSES), PC.result.is_not(None))
                .order_by(S.started_at.desc(), R.session_id, R.procedure_id, R.kind, R.index)
                .limit(SCAN)
            ).all()
            labeled = {(x.session_id, x.procedure_id, x.rule_id) for x in db.exec(select(RuleLabel).where(RuleLabel.session_id.in_({r.session_id for r, *_ in rows})))} if rows else set()
        weights = [weight(r) for r, *_ in rows]
        mean = sum(weights) / len(weights) if weights else 1.0
        picked = [(row, w) for row, w in zip(rows, weights) if draw(row[0].session_id, row[0].procedure_id, row[0].rule_id) < min(1.0, rate * w / mean)]
        pending = [(row, w) for row, w in picked if (row[0].session_id, row[0].procedure_id, row[0].rule_id) not in labeled]
        pending.sort(key=lambda x: (-x[1], -_utc(x[0][1]).timestamp()))  # type: ignore[union-attr]
        variables: dict[tuple[int, str | None], dict[str, str]] = {}
        items = []
        for (r, started, agent, name, release), w in pending[:limit]:
            if release is not None and (release, agent) not in variables:
                variables[(release, agent)] = agent_variables(self.store, ws, release, agent)
            items.append({
                "session_id": r.session_id, "started_at": _utc(started), "procedure_id": r.procedure_id, "procedure_name": name, "rule_id": r.rule_id,
                "kind": r.kind, "index": r.index, "text": fill(r.rule_text, variables.get((release, agent), {})), "status": r.status,
                "evidence": r.evidence or [], "reason": (r.reason or "").split(LENIENT_MARK)[0] or None, "decision_p": r.decision_p,
                "decision_threshold": r.decision_threshold, "decided_status": r.decided_status, "reviewed": r.reviewed, "weight": round(w, 3),
                "why": why(r), "statuses": list(STATUSES[r.kind]),
            })
        return {"items": items, "total": len(pending), "answered": len(picked) - len(pending), "rate": rate, "scanned": len(rows)}
