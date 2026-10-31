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

**Spot checks** come in batches, so the work doesn't grow with call volume. The queue holds one batch (the
workspace's `spot_check_batch`, default `SPOT_CHECK_BATCH`), drawn the first time it's asked for; after that a new batch
is drawn only on request (`draw`). A batch is spread across procedures and agents: the candidates (recent rule results
with no finding, never drawn or labeled before) are grouped by procedure and agent, each group sorted by value
(`weight`: close calls, and decisions the reasoning model's review changed, first), and the batch takes one from each
group in turn, the most valuable group first. An item is answered by any label on its rule after it was drawn; one left
unanswered for `EXPIRE_DAYS` expires quietly: not shown, not counted, never a label.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from .errors import InvalidValue, NotFound
from .orm import Finding, FindingOverride, ProcedureCheck, RuleLabel, RuleResult, SessionRecord, SpotCheck, Workspace
from .pipeline.procedures import agent_variables, fill
from .pipeline.rollup import FINDING_STATUSES, NEVER_STATUSES, WARNING_STATUSES

if TYPE_CHECKING:
    from .store import Store

STATUSES = {"step": ("done", "out_of_order", "skipped", "not_reached", "not_applicable"), "option": ("done", "skipped", "not_reached", "not_applicable"),  # order is judged on the step
            "never": NEVER_STATUSES, "warning": WARNING_STATUSES}
SOURCES = ("correction", "spot_check")
SPOT_CHECK_BATCH = 25
MAX_BATCH = 200
EXPIRE_DAYS = 7
SCAN = 5000  # the most recent rule results a batch is drawn from
WHY = ("close_call", "review_changed", "sample")
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
            for sc in db.exec(select(SpotCheck).where(SpotCheck.session_id == session_id, SpotCheck.procedure_id == procedure_id, SpotCheck.rule_id == rule_id,
                                                      SpotCheck.answered_at.is_(None))):
                sc.answered_at, sc.label_id = row.created_at, row.id  # any label answers it
                db.add(sc)
        self.store.verdicts.refresh(ws, sessions=[session_id])
        with self._db() as db:
            f = db.exec(select(Finding).where(Finding.session_id == session_id, Finding.procedure_id == procedure_id, Finding.rule_id == rule_id)).first()
        return label_out(row, f.suppressed if f else None)

    def set_batch(self, ws: str, batch: int | None) -> dict[str, Any]:
        """The workspace's spot-check batch size (None: back to the default)."""
        if batch is not None and not 1 <= batch <= MAX_BATCH:
            raise InvalidValue(f"batch is between 1 and {MAX_BATCH}")
        with self._db() as db, db.begin():
            w = db.get(Workspace, ws)
            if w is None:
                raise NotFound(f"no workspace '{ws}'")
            w.spot_check_batch = batch
            db.add(w)
        return self.settings(ws)

    def draw(self, ws: str, size: int | None = None) -> dict[str, Any]:
        """Draw a new batch (see the module docstring) and return the queue with it."""
        size = size or self.settings(ws)["batch"]
        if not 1 <= size <= MAX_BATCH:
            raise InvalidValue(f"size is between 1 and {MAX_BATCH}")
        R, S, PC = RuleResult, SessionRecord, ProcedureCheck
        with self._db() as db, db.begin():
            rows = db.exec(
                select(R, S.agent_id, S.started_at).join(S, S.id == R.session_id)
                .join(PC, (PC.session_id == R.session_id) & (PC.procedure_id == R.procedure_id))
                .where(R.workspace_id == ws, R.status.not_in(FINDING_STATUSES), PC.result.is_not(None))
                .order_by(S.started_at.desc()).limit(SCAN)
            ).all()
            sids = {r.session_id for r, *_ in rows}
            done = {(x.session_id, x.procedure_id, x.rule_id) for x in db.exec(select(SpotCheck).where(SpotCheck.workspace_id == ws, SpotCheck.session_id.in_(sids)))} if sids else set()
            done |= {(x.session_id, x.procedure_id, x.rule_id) for x in db.exec(select(RuleLabel).where(RuleLabel.session_id.in_(sids)))} if sids else set()
            groups: dict[tuple[str, str | None], list[tuple[float, datetime, RuleResult]]] = defaultdict(list)
            for r, agent, started in rows:
                if (r.session_id, r.procedure_id, r.rule_id) not in done:
                    groups[(r.procedure_id, agent)].append((weight(r), _utc(started), r))  # type: ignore[arg-type]
            for g in groups.values():
                g.sort(key=lambda x: (-x[0], -x[1].timestamp()))
            order = sorted(groups.values(), key=lambda g: (-g[0][0], -g[0][1].timestamp()))
            picked: list[tuple[float, RuleResult]] = []
            while len(picked) < size and any(order):
                for g in order:
                    if g and len(picked) < size:
                        w, _, r = g.pop(0)
                        picked.append((w, r))
            batch, now = str(uuid.uuid4()), _now()
            for w, r in picked:
                db.add(SpotCheck(id=str(uuid.uuid4()), workspace_id=ws, batch_id=batch, session_id=r.session_id, procedure_id=r.procedure_id, rule_id=r.rule_id,
                                 rule_result_id=r.id, weight=round(w, 3), why=",".join(why(r)), drawn_at=now))
        return self.spot_checks(ws, draw=False)

    # --- reads -----------------------------------------------------------------------------------------------------

    def settings(self, ws: str) -> dict[str, Any]:
        with self._db() as db:
            w = db.get(Workspace, ws)
        own = w.spot_check_batch if w else None
        return {"batch": SPOT_CHECK_BATCH if own is None else own, "default": own is None}

    def for_session(self, session_id: str) -> dict[tuple[str, str], Call]:
        """{(procedure id, rule id): the latest call} for one session."""
        with self._db() as db:
            return {(pid, rid): c for (_, pid, rid), c in latest_calls(db, [session_id]).items()}

    def spot_checks(self, ws: str, draw: bool = True, agent: str | None = None, procedure: str | None = None, rule: str | None = None,
                    since: datetime | None = None, until: datetime | None = None, why_: str | None = None, sort: str = "value") -> dict[str, Any]:
        """The current batch: its progress over every live item (answered, or drawn less than `EXPIRE_DAYS` ago), and its
        items, filtered and sorted (`value` or `newest`). The workspace's first batch is drawn when it has none."""
        with self._db() as db:
            latest = db.exec(select(SpotCheck.batch_id, SpotCheck.drawn_at).where(SpotCheck.workspace_id == ws).order_by(SpotCheck.drawn_at.desc()).limit(1)).first()
        if latest is None:
            return self.draw(ws) if draw else self._empty(ws)
        batch, drawn_at = latest
        cutoff = _now() - timedelta(days=EXPIRE_DAYS)
        R, S, PC = RuleResult, SessionRecord, ProcedureCheck
        with self._db() as db:
            rows = db.exec(
                select(SpotCheck, R, S.started_at, S.agent_id, PC.procedure_name, PC.release_id).join(S, S.id == SpotCheck.session_id)
                .join(R, (R.session_id == SpotCheck.session_id) & (R.procedure_id == SpotCheck.procedure_id) & (R.rule_id == SpotCheck.rule_id))
                .join(PC, (PC.session_id == SpotCheck.session_id) & (PC.procedure_id == SpotCheck.procedure_id))
                .where(SpotCheck.workspace_id == ws, SpotCheck.batch_id == batch)
            ).all()
            labels = {x.id: x for x in db.exec(select(RuleLabel).where(RuleLabel.id.in_([sc.label_id for sc, *_ in rows if sc.label_id])))}
        live = [x for x in rows if x[0].answered_at is not None or _utc(x[0].drawn_at) > cutoff]  # type: ignore[operator]
        answered = [x for x in live if x[0].answered_at is not None]
        corrections = sum(1 for sc, *_ in answered if (lab := labels.get(sc.label_id or "")) and lab.status != lab.checked_status)
        variables: dict[tuple[int, str | None], dict[str, str]] = {}
        items = []
        for sc, r, started, agent_id, name, release in live:
            reasons = sc.why.split(",")
            if (agent and agent_id != agent) or (procedure and sc.procedure_id != procedure) or (rule and sc.rule_id != rule) or (why_ and why_ not in reasons):
                continue
            if (since and _utc(started) < _utc(since)) or (until and _utc(started) > _utc(until)):  # type: ignore[operator]
                continue
            if release is not None and (release, agent_id) not in variables:
                variables[(release, agent_id)] = agent_variables(self.store, ws, release, agent_id)
            lab = labels.get(sc.label_id or "")
            items.append({
                "spot_check_id": sc.id, "session_id": sc.session_id, "started_at": _utc(started), "agent_id": agent_id, "procedure_id": sc.procedure_id,
                "procedure_name": name, "rule_id": sc.rule_id, "kind": r.kind, "index": r.index, "text": fill(r.rule_text, variables.get((release, agent_id), {})),
                "status": r.status, "evidence": r.evidence or [], "reason": (r.reason or "").split(LENIENT_MARK)[0] or None, "decision_p": r.decision_p,
                "decision_threshold": r.decision_threshold, "decided_status": r.decided_status, "reviewed": r.reviewed, "weight": sc.weight, "why": reasons,
                "statuses": list(STATUSES[r.kind]), "answered": sc.answered_at is not None, "answer": lab.status if lab else None,
            })
        if sort == "newest":
            items.sort(key=lambda x: (-x["started_at"].timestamp(), x["session_id"], x["kind"], x["index"]))
        else:
            items.sort(key=lambda x: (-x["weight"], -x["started_at"].timestamp(), x["session_id"], x["kind"], x["index"]))
        return {"batch_id": batch, "drawn_at": _utc(drawn_at), "size": len(live), "answered": len(answered), "corrections": corrections,
                "done": bool(live) and len(answered) == len(live), "items": items, "default_batch": self.settings(ws)["batch"]}

    def _empty(self, ws: str) -> dict[str, Any]:
        return {"batch_id": None, "drawn_at": None, "size": 0, "answered": 0, "corrections": 0, "done": False, "items": [], "default_batch": self.settings(ws)["batch"]}
