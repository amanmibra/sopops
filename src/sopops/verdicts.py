"""Verdicts and overrides: what a reviewer says about a pattern or one finding, and what follows from it.

A **verdict** on a pattern (a rule pattern or a sub-pattern; ids are stable across reclustering) is one of:

- `real`: a real agent mistake. Its findings keep counting. A suggestion can change the instruction to prevent it.
- `acceptable`: the behavior is fine in this situation. Its findings, now and later, are **suppressed**. A suggestion
  can add the exception to the rule.
- `sop_wrong`: the instruction is wrong or outdated. A suggestion can edit the rule.
- `checker_wrong`: sopops misjudged. Its findings are suppressed, and are negative labels for the workspace's
  calibration (`calibration_examples`).

Verdict rows are only appended; a pattern's latest is its current verdict. A sub-pattern without one of its own has
its rule pattern's (`inherited`). A recluster that retires a sub-pattern carries its verdict to its successor
(`carry_over`, called by `sopops.patterns`).

An **override** says the checker was right or wrong about one finding (`finding_overrides`); it's kept by session,
procedure and rule, so it outlives a re-check. A wrong finding is suppressed and a negative label, like
`checker_wrong`; a right one overrides a `checker_wrong` pattern for that finding. `acceptable` suppresses either way.

A pattern's **review state** (`state`) follows its verdict and the fix:
- `needs_review`: no verdict; a verdict carried over by a recluster, not confirmed; or a verdict older than a
  recluster that changed the pattern's members materially (`Pattern.clustered_at`).
- `closed`: acceptable or checker_wrong. Its findings are suppressed; nothing to fix. A rule reworded after the verdict
  (`sopops.lineage`) has findings the verdict didn't judge: those aren't suppressed (nor calibration labels), and the
  pattern needs review again until a verdict confirms it for the new wording.
- `open`: real or sop_wrong, with no suggestion applied for this verdict.
- `fixing`: a suggestion for it was applied (a draft, or a pull request, open or merged), and the release with the
  edit (the first whose file is the suggested text: for a repo, the pull of the merge commit, then a publish) isn't
  live yet, or is live with fewer than `FIXED_MIN_N` sessions since. A pull request closed without merging dismisses
  the suggestion, so the pattern is `open` again.
- `fixed`: the release is live and `FIXED_MIN_N` sessions checked on the procedure ran it or a later one, with no
  finding in the pattern; `regressed`: a finding in the pattern came back on such a session.
The review queue lists `needs_review` and `regressed` by default.

**Suppressed** findings are kept and flagged; Insights leaves them out of every count and rate. Each procedure
check's `result` is rolled up again with its suppressed rules read as passed (`checked_result` keeps the checker's
own), and the session's result is the worst of its checks, so a session whose only deviation is suppressed counts
as followed. `refresh` brings this in line after every verdict, override, assignment and recluster.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import dspy
from sqlmodel import Session, and_, func, or_, select

from . import lineage
from .errors import Conflict, NotFound
from .orm import (
    Finding,
    FindingOverride,
    Pattern,
    ProcedureCheck,
    FileVersion,
    Release,
    RuleLineage,
    RuleResult,
    SessionRecord,
    Suggestion,
    Theme,
    Verdict,
    Workspace,
)
from .pipeline.rollup import Judged, rollup, worst

if TYPE_CHECKING:
    from .store import Store

VERDICTS = ("real", "acceptable", "sop_wrong", "checker_wrong")
SUPPRESSING = frozenset({"acceptable", "checker_wrong"})
SUGGESTIBLE = frozenset({"real", "acceptable", "sop_wrong"})
PASS_STATUS = {"step": "done", "never": "ok", "warning": "present"}  # a suppressed rule result reads as this
LENIENT_MARK = "(Lenient:"  # in the reason of a skipped step the leniency rule kept from deviating (see checks.save)
EXAMPLES = 5
REVIEW_STATES = ("needs_review", "open", "fixing", "fixed", "regressed", "closed")
QUEUE_STATES = ("needs_review", "regressed")  # what the review queue lists by default
FIXED_MIN_N = 10  # sessions checked on the procedure on the fix's release or later, with none in the pattern, before it's fixed


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime | None) -> datetime | None:
    if at is None:
        return None
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)


def verdict_out(v: Verdict, inherited: bool = False) -> dict[str, Any]:
    return {"verdict_id": v.id, "pattern_id": v.pattern_id, "verdict": v.verdict, "note": v.note, "reviewer": v.reviewer,
            "carried_from": v.carried_from, "inherited": inherited, "created_at": _utc(v.created_at)}


def override_out(o: FindingOverride, suppressed: bool | None = None) -> dict[str, Any]:
    return {"override_id": o.id, "finding_id": o.finding_id, "session_id": o.session_id, "procedure_id": o.procedure_id, "rule_id": o.rule_id,
            "correct": o.correct, "note": o.note, "reviewer": o.reviewer, "created_at": _utc(o.created_at), "suppressed": suppressed}


def suggestion_summary(s: Suggestion, pattern_name: str | None = None) -> dict[str, Any]:
    return {"suggestion_id": s.id, "pattern_id": s.pattern_id, "pattern_name": pattern_name, "procedure_id": s.procedure_id, "rule_id": s.rule_id,
            "block_id": s.procedure_id, "status": s.status, "rationale": s.rationale, "error": s.error, "pr_url": s.pr_url,
            "created_at": _utc(s.created_at), "updated_at": _utc(s.updated_at)}


def latest(db: Session, ws: str, pattern_ids: Iterable[str]) -> dict[str, Verdict]:
    """Each pattern's own current verdict."""
    ids = list(set(pattern_ids))
    if not ids:
        return {}
    out: dict[str, Verdict] = {}
    for v in db.exec(select(Verdict).where(Verdict.workspace_id == ws, Verdict.pattern_id.in_(ids)).order_by(Verdict.created_at)):
        out[v.pattern_id] = v
    return out


def effective(db: Session, ws: str, patterns: Iterable[Pattern]) -> dict[str, tuple[Verdict, bool]]:
    """{pattern id: (verdict, inherited)}: its own, else its rule pattern's."""
    pats = list(patterns)
    own = latest(db, ws, [p.id for p in pats] + [p.parent_id for p in pats if p.parent_id])
    out = {}
    for p in pats:
        if p.id in own:
            out[p.id] = (own[p.id], False)
        elif p.parent_id and p.parent_id in own:
            out[p.id] = (own[p.parent_id], True)
    return out


def carry_over(db: Session, ws: str, pairs: list[tuple[str, str]], now: datetime) -> None:
    """(retired pattern, successor): the successor gets the retired one's current verdict, unless it has its own."""
    if not pairs:
        return
    own = latest(db, ws, [p for pair in pairs for p in pair])
    for old, new in pairs:
        v = own.get(old)
        if v is None or new in own:
            continue
        own[new] = Verdict(id=str(uuid.uuid4()), workspace_id=ws, pattern_id=new, verdict=v.verdict, note=v.note, reviewer=v.reviewer, carried_from=old, created_at=now)
        db.add(own[new])


def reviewed_result(pc: ProcedureCheck, rules: list[RuleResult], passed: set[str]) -> str | None:
    """A procedure check's result with the rule results in `passed` read as passed. `required` isn't stored on a rule
    result, but only matters for a skipped step when the goal was reached, and then the leniency mark says it."""
    if not passed or pc.checked_result in (None, "not_measurable"):
        return pc.checked_result
    judged = [
        Judged(r.rule_id, r.kind, r.index, PASS_STATUS.get(r.kind, r.status) if r.id in passed else r.status, [], "",
               r.kind == "step" and r.status == "skipped" and LENIENT_MARK not in (r.reason or ""))
        for r in rules
    ]
    return rollup(judged, pc.goal_reached).result


def _overrides(db: Session, session_ids: Iterable[str]) -> dict[tuple[str, str, str], FindingOverride]:
    """The latest override per (session, procedure, rule)."""
    ids = list(set(session_ids))
    if not ids:
        return {}
    out = {}
    for o in db.exec(select(FindingOverride).where(FindingOverride.session_id.in_(ids)).order_by(FindingOverride.created_at)):
        out[(o.session_id, o.procedure_id, o.rule_id)] = o
    return out


def _labels(db: Session, ws: str, findings: list[Finding]) -> dict[str, tuple[str | None, FindingOverride | None]]:
    """{finding id: (its pattern's verdict, its override)}. A suppressing verdict doesn't reach a finding on a wording
    of the rule published after it: it judged the rule as it was worded then."""
    pids = {f.pattern_id for f in findings if f.pattern_id}
    pats = list(db.exec(select(Pattern).where(Pattern.id.in_(pids)))) if pids else []
    verdicts = effective(db, ws, pats)
    overrides = _overrides(db, [f.session_id for f in findings])
    published = _published(db, ws, {f.rule_id for f in findings})
    out = {}
    for f in findings:
        v = verdicts.get(f.pattern_id or "")
        label = v[0].verdict if v else None
        if v and label in SUPPRESSING and (at := published.get((f.procedure_id, f.rule_id))) and at > _utc(v[0].created_at):
            label = None
        out[f.id] = (label, overrides.get((f.session_id, f.procedure_id, f.rule_id)))
    return out


def _published(db: Session, ws: str, rule_ids: set[str]) -> dict[tuple[str, str], datetime]:
    """{(procedure id, rule id): when the release that reworded the rule into it was made}, for reworded rules."""
    if not rule_ids:
        return {}
    L = RuleLineage
    q = select(L.procedure_id, L.rule_id, Release.created_at).join(Release, and_(Release.workspace == L.workspace_id, Release.number == L.release_id))
    return {(pid, rid): _utc(at) for pid, rid, at in db.exec(q.where(L.workspace_id == ws, L.rule_id.in_(rule_ids))).all()}  # type: ignore[misc]


def _recompute(db: Session, checks: set[tuple[str, str]]) -> None:
    if not checks:
        return
    sids = {s for s, _ in checks}
    passed = set(db.exec(select(Finding.rule_result_id).where(Finding.session_id.in_(sids), Finding.suppressed.is_(True))))
    rules: dict[tuple[str, str], list[RuleResult]] = defaultdict(list)
    for r in db.exec(select(RuleResult).where(RuleResult.session_id.in_(sids))):
        rules[(r.session_id, r.procedure_id)].append(r)
    for pc in db.exec(select(ProcedureCheck).where(ProcedureCheck.session_id.in_(sids))):
        if (pc.session_id, pc.procedure_id) in checks and pc.checked_result is not None:
            mine = rules.get((pc.session_id, pc.procedure_id), [])
            pc.result = reviewed_result(pc, mine, {r.id for r in mine if r.id in passed})
            db.add(pc)
    db.flush()
    for s in db.exec(select(SessionRecord).where(SessionRecord.id.in_(sids), SessionRecord.result.is_not(None))):
        s.result = worst(list(db.exec(select(ProcedureCheck.result).where(ProcedureCheck.session_id == s.id, ProcedureCheck.result.is_not(None)))))
        db.add(s)


def members(p: Pattern):
    """The query condition for a pattern's findings: a rule pattern's are every finding on its rule, in any wording
    (its `rule_id` is the lineage id; see `sopops.lineage`)."""
    if p.kind == "rule":
        return and_(Finding.workspace_id == p.workspace_id, Finding.procedure_id == p.procedure_id, lineage.in_lineage(Finding.rule_id, p.workspace_id, p.procedure_id, p.rule_id))
    return Finding.pattern_id == p.id


def refresh_findings(db: Session, ws: str, rules: Iterable[tuple[str, str]] = (), sessions: Iterable[str] = ()) -> int:
    """`Verdicts.refresh` in the caller's transaction."""
    rules, sessions = set(rules), set(sessions)
    if not rules and not sessions:
        return 0
    conds = [Finding.session_id.in_(sessions)] if sessions else []
    conds += [and_(Finding.procedure_id == pid, lineage.in_lineage(Finding.rule_id, ws, pid, lineage.lineage_of(db, ws, pid, rid))) for pid, rid in rules]
    findings = list(db.exec(select(Finding).where(Finding.workspace_id == ws, or_(*conds))))
    labels = _labels(db, ws, findings)
    touched: set[tuple[str, str]] = set()
    for f in findings:
        want = Verdicts.suppressed_by(*labels[f.id])
        if f.suppressed != want:
            f.suppressed = want
            db.add(f)
            touched.add((f.session_id, f.procedure_id))
    db.flush()
    _recompute(db, touched)
    return len(touched)


class Verdicts:
    def __init__(self, store: Store):
        self.store = store

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    def _pattern(self, db: Session, ws: str, pattern_id: str) -> Pattern:
        p = db.get(Pattern, pattern_id)
        if p is None or p.workspace_id != ws:
            raise NotFound(f"no pattern '{pattern_id}'")
        return p

    # --- writes --------------------------------------------------------------------------------------------------

    def set(self, ws: str, pattern_id: str, verdict: str, note: str | None = None, reviewer: str | None = None) -> dict[str, Any]:
        """Give a pattern a verdict (its history is kept), then suppress or unsuppress its findings."""
        if verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
        with self._db() as db, db.begin():
            p = self._pattern(db, ws, pattern_id)
            v = Verdict(id=str(uuid.uuid4()), workspace_id=ws, pattern_id=p.id, verdict=verdict, note=note or None, reviewer=reviewer or None, created_at=_now())
            db.add(v)
        self.refresh(ws, rules=[(p.procedure_id, p.rule_id)])
        return {**verdict_out(v), "suppressed_count": self.suppressed_count(ws, p)}

    def override(self, ws: str, finding_id: str, correct: bool, note: str | None = None, reviewer: str | None = None) -> dict[str, Any]:
        with self._db() as db, db.begin():
            f = db.get(Finding, finding_id)
            if f is None or f.workspace_id != ws:
                raise NotFound(f"no finding '{finding_id}'")
            o = FindingOverride(id=str(uuid.uuid4()), workspace_id=ws, finding_id=f.id, session_id=f.session_id, procedure_id=f.procedure_id, rule_id=f.rule_id,
                                correct=correct, note=note or None, reviewer=reviewer or None, created_at=_now())
            db.add(o)
        self.refresh(ws, sessions=[f.session_id])
        with self._db() as db:
            now = db.get(Finding, f.id)
        return override_out(o, now.suppressed if now else None)

    # --- suppression ---------------------------------------------------------------------------------------------

    @staticmethod
    def suppressed_by(verdict: str | None, override: FindingOverride | None) -> bool:
        if verdict == "acceptable":
            return True
        if override is not None:
            return not override.correct
        return verdict == "checker_wrong"

    @staticmethod
    def wrong(verdict: str | None, override: FindingOverride | None) -> bool | None:
        """Whether the checker got the finding wrong: True, False, or None when nobody has said."""
        if override is not None:
            return not override.correct
        return None if verdict is None else verdict == "checker_wrong"

    def refresh(self, ws: str, rules: Iterable[tuple[str, str]] = (), sessions: Iterable[str] = ()) -> int:
        """Bring the findings of these rules ((procedure id, rule id); every rule of its lineage) and sessions in line
        with the verdicts and overrides, and roll up again the checks whose suppressed findings changed. Returns how
        many checks changed."""
        rules, sessions = set(rules), set(sessions)
        if not rules and not sessions:
            return 0
        with self._db() as db, db.begin():
            return refresh_findings(db, ws, rules, sessions)

    # --- reads ---------------------------------------------------------------------------------------------------

    def _members(self, p: Pattern):
        return members(p)

    def suppressed_count(self, ws: str, p: Pattern) -> int:
        with self._db() as db:
            return db.exec(select(func.count(Finding.id)).where(self._members(p), Finding.suppressed.is_(True))).one()

    def for_pattern(self, ws: str, pattern_id: str) -> dict[str, Any]:
        """What the pattern page adds: the current verdict, its history (newest first), suppressed findings (all
        time) and its suggestions (newest first)."""
        with self._db() as db:
            p = self._pattern(db, ws, pattern_id)
            v = effective(db, ws, [p]).get(p.id)
            history = list(db.exec(select(Verdict).where(Verdict.workspace_id == ws, Verdict.pattern_id == p.id).order_by(Verdict.created_at.desc())))
            sugg = list(db.exec(select(Suggestion).where(Suggestion.workspace_id == ws, Suggestion.pattern_id == p.id).order_by(Suggestion.created_at.desc())))
            state = self.state(db, ws, p, v)
        return {"verdict": verdict_out(*v) if v else None, "verdict_history": [verdict_out(x) for x in history],
                "suppressed_count": self.suppressed_count(ws, p), "suggestions": [suggestion_summary(s, p.name) for s in sugg], **state}

    def annotate(self, ws: str, items: list[dict[str, Any]]) -> None:
        """Add `verdict`, `review_state` and `review_note` to pattern dicts (the patterns list)."""
        with self._db() as db:
            pats = list(db.exec(select(Pattern).where(Pattern.id.in_([x["pattern_id"] for x in items])))) if items else []
            verdicts = effective(db, ws, pats)
            by_id = {p.id: p for p in pats}
            for x in items:
                p = by_id.get(x["pattern_id"])
                if p is None:
                    continue
                v = verdicts.get(p.id)
                state = self.state(db, ws, p, v)
                x.update(verdict=verdict_out(*v) if v else None, review_state=state["review_state"], review_note=state["review_note"])

    def clustered_at(self, ws: str) -> datetime | None:
        """The workspace's last clustering of any rule."""
        with self._db() as db:
            return _utc(db.exec(select(func.max(Pattern.clustered_at)).where(Pattern.workspace_id == ws, Pattern.kind == "rule")).one())

    def state(self, db: Session, ws: str, p: Pattern, v: tuple[Verdict, bool] | None) -> dict[str, Any]:
        """{review_state, review_note, fix_release} (see the module docstring)."""
        def out(state: str, note: str | None = None, release: int | None = None) -> dict[str, Any]:
            return {"review_state": state, "review_note": note, "fix_release": release}

        if v is None:
            return out("needs_review", "No verdict yet.")
        verdict = v[0]
        if verdict.carried_from:
            return out("needs_review", "Carried over from a failure mode a recluster retired; confirm it.")
        if p.kind != "rule" and p.clustered_at is not None and _utc(verdict.created_at) < _utc(p.clustered_at):
            return out("needs_review", "A recluster changed its findings since the verdict.")
        if verdict.verdict in SUPPRESSING:
            L = RuleLineage
            newer = db.exec(select(func.count(Finding.id)).join(L, and_(L.workspace_id == Finding.workspace_id, L.procedure_id == Finding.procedure_id, L.rule_id == Finding.rule_id))
                            .join(Release, and_(Release.workspace == L.workspace_id, Release.number == L.release_id))
                            .where(self._members(p), Release.created_at > verdict.created_at)).one()
            if newer:
                return out("needs_review", f"The rule was reworded after this verdict: {newer} finding{'s' if newer != 1 else ''} on the new wording, not suppressed. Confirm it, or give another.")
            return out("closed")
        fix = db.exec(select(Suggestion).where(Suggestion.workspace_id == ws, Suggestion.pattern_id.in_([x for x in (p.id, p.parent_id) if x]),
                                               Suggestion.verdict_id == verdict.id, Suggestion.status.in_(("applied", "pr_open", "merged")))
                      .order_by(Suggestion.updated_at.desc())).first()
        if fix is None:
            return out("open")
        release = self._release_with(db, ws, fix)
        live = db.exec(select(Workspace.current_release).where(Workspace.workspace == ws)).first()
        if release is None:
            wait = {"applied": "Waiting for a release with the edit.", "pr_open": "Waiting for the pull request to be merged.",
                    "merged": "Merged; waiting for a release with the change (pull the repo, then publish)."}
            return out("fixing", wait[fix.status])
        if live is None or live < release:
            return out("fixing", f"Release {release} has the edit; it isn't live yet.", release)
        S, F, PC = SessionRecord, Finding, ProcedureCheck
        # the edit gave the rule a new id; its lineage keeps it in the pattern, so findings on the new wording count
        back = db.exec(select(func.count(F.id)).join(S, S.id == F.session_id)
                       .where(F.workspace_id == ws, self._members(p), F.suppressed.is_(False), S.release_id >= release)).one()
        if back:
            return out("regressed", f"Came back after release {release}: {back} finding{'s' if back != 1 else ''} on sessions that ran it or later.", release)
        n = db.exec(select(func.count(func.distinct(PC.session_id))).join(S, S.id == PC.session_id)
                    .where(PC.workspace_id == ws, PC.procedure_id == p.procedure_id, PC.result.is_not(None), S.release_id >= release)).one()
        if n >= FIXED_MIN_N:
            return out("fixed", f"No findings in {n} sessions on release {release} or later.", release)
        return out("fixing", f"Release {release} is live: {n} of {FIXED_MIN_N} sessions so far.", release)

    def _release_with(self, db: Session, ws: str, s: Suggestion) -> int | None:
        """The first release since the suggestion whose file is its suggested text."""
        if not s.path or s.new_text is None:
            return None
        for rel in db.exec(select(Release).where(Release.workspace == ws, Release.created_at >= s.created_at).order_by(Release.number)):
            version = rel.files.get(s.path)
            if version is None:
                continue
            content = db.exec(select(FileVersion.content).where(FileVersion.workspace == ws, FileVersion.path == s.path, FileVersion.version == version)).first()
            if content == s.new_text:
                return rel.number
        return None

    def queue(self, ws: str, limit: int = 50, states: Iterable[str] | None = None) -> dict[str, Any]:
        """Every active leaf pattern (a rule pattern that isn't split, else its sub-patterns and `other`) with findings,
        in the review states asked for (default `QUEUE_STATES`), most sessions first; `counts` has every state."""
        want = set(states or QUEUE_STATES)
        with self._db() as db:
            pats = list(db.exec(select(Pattern).where(Pattern.workspace_id == ws, Pattern.status == "active")))
            split = {p.parent_id for p in pats if p.kind == "sub"}
            leaves = [p for p in pats if p.finding_count and ((p.kind == "rule" and p.id not in split) or (p.kind != "rule" and p.parent_id in split))]
            verdicts = effective(db, ws, leaves)
            counts = dict.fromkeys(REVIEW_STATES, 0)
            items = []
            for p in leaves:
                v = verdicts.get(p.id)
                state = self.state(db, ws, p, v)
                counts[state["review_state"]] += 1
                if state["review_state"] in want:
                    items.append((p, v, state))
            items.sort(key=lambda x: (-x[0].session_count, -x[0].finding_count, x[0].name, x[0].id))
            out = [self._item(db, ws, p, v, state) for p, v, state in items[:limit]]
        return {"items": out, "total": len(items), "counts": counts, "clustered_at": self.clustered_at(ws)}

    def _item(self, db: Session, ws: str, p: Pattern, v: tuple[Verdict, bool] | None, state: dict[str, Any]) -> dict[str, Any]:
        S, R, F = SessionRecord, RuleResult, Finding
        rows = db.exec(
            select(F, R, S.started_at).join(R, R.id == F.rule_result_id).join(S, S.id == F.session_id)
            .where(self._members(p)).order_by(F.suppressed, S.started_at.desc()).limit(EXAMPLES)
        ).all()
        text = db.exec(select(R.kind, R.index, R.rule_text).join(S, S.id == R.session_id)  # the wording its rule has now
                       .where(R.workspace_id == ws, R.procedure_id == p.procedure_id, lineage.in_lineage(R.rule_id, ws, p.procedure_id, p.rule_id))
                       .order_by(S.started_at.desc()).limit(1)).first()
        theme = db.get(Theme, p.theme_id) if p.theme_id else None
        name = db.exec(select(ProcedureCheck.procedure_name).where(ProcedureCheck.workspace_id == ws, ProcedureCheck.procedure_id == p.procedure_id).limit(1)).first()
        sugg = db.exec(select(Suggestion).where(Suggestion.workspace_id == ws, Suggestion.pattern_id == p.id).order_by(Suggestion.created_at.desc()).limit(1)).first()
        suppressed = db.exec(select(func.count(F.id)).where(self._members(p), F.suppressed.is_(True))).one()
        new = db.exec(select(func.count(F.id)).where(self._members(p), F.suppressed.is_(False), *([F.created_at > v[0].created_at] if v else []))).one()
        return {
            "pattern_id": p.id, "procedure_id": p.procedure_id, "rule_id": p.rule_id, "parent_id": p.parent_id, "kind": p.kind, "name": p.name,
            "description": p.description, "theme_id": p.theme_id, "theme_name": theme.name if theme else None, "status": p.status,
            "findings": p.finding_count, "sessions": p.session_count, "first_seen": _utc(p.first_seen), "last_seen": _utc(p.last_seen),
            "clustered_at": _utc(p.clustered_at), "procedure_name": name or p.procedure_id, "rule": {"kind": text[0], "index": text[1], "text": text[2]} if text else {"kind": None, "index": None, "text": None},
            "rule_text": text[2] if text else None, "examples": [_example(f, r, at) for f, r, at in rows], "verdict": verdict_out(*v) if v else None,
            "new_findings": new, "suppressed_count": suppressed, "suggestion": suggestion_summary(sugg, p.name) if sugg else None, **state,
        }

    def examples(self, ws: str, pattern_id: str, k: int = EXAMPLES) -> tuple[Pattern, list[dict[str, Any]]]:
        """A pattern's most recent findings (suppressed ones last), for a suggestion's prompt."""
        with self._db() as db:
            p = self._pattern(db, ws, pattern_id)
            S, R, F = SessionRecord, RuleResult, Finding
            rows = db.exec(select(F, R, S.started_at).join(R, R.id == F.rule_result_id).join(S, S.id == F.session_id)
                           .where(self._members(p)).order_by(F.suppressed, S.started_at.desc()).limit(k)).all()
        return p, [_example(f, r, at) for f, r, at in rows]

    def effective_verdict(self, ws: str, pattern_id: str) -> tuple[Pattern, Verdict | None]:
        with self._db() as db:
            p = self._pattern(db, ws, pattern_id)
            v = effective(db, ws, [p]).get(p.id)
        return p, v[0] if v else None

    # --- regression export ---------------------------------------------------------------------------------------

    def regression(self, ws: str, pattern_id: str) -> list[dict[str, Any]]:
        """One line per finding in the pattern (a session has at most one per rule): what a fixed agent or checker
        should do with that session. `expect`: `agent` (a `real` mistake: replay the conversation against the fixed
        prompt; the agent should now pass the rule) or `checker` (`acceptable`, `sop_wrong`, `checker_wrong`, or a
        finding overridden as wrong: re-check this transcript; the rule should come out `expected_status`)."""
        S, R, F = SessionRecord, RuleResult, Finding
        with self._db() as db:
            p = self._pattern(db, ws, pattern_id)
            v = effective(db, ws, [p]).get(p.id)
            if v is None:
                raise Conflict("give the pattern a verdict first")
            rows = db.exec(select(F, R, S).join(R, R.id == F.rule_result_id).join(S, S.id == F.session_id).where(self._members(p)).order_by(S.started_at, F.id)).all()
            overrides = _overrides(db, [f.session_id for f, _r, _s in rows])
        out = []
        for f, r, s in rows:
            o = overrides.get((f.session_id, f.procedure_id, f.rule_id))
            label = "checker_wrong" if self.wrong(v[0].verdict, o) else v[0].verdict
            out.append({
                "session_id": s.id, "external_id": s.external_id, "platform": s.platform, "agent": s.agent_id or s.agent_ref, "release": s.release_id,
                "started_at": _utc(s.started_at).isoformat(), "transcript_ref": f"/v1/workspaces/{ws}/sessions/{s.id}", "finding_id": f.id,
                "pattern_id": p.id, "procedure_id": f.procedure_id, "rule_id": f.rule_id, "rule_kind": r.kind, "rule_index": r.index, "rule_text": r.rule_text,
                "verdict": label, "observed_status": r.status, "expected_status": PASS_STATUS.get(r.kind, r.status),
                "expect": "agent" if label == "real" else "checker",
            })
        return out

    # --- calibration -----------------------------------------------------------------------------------------------

    def calibration_examples(self, ws: str, procedure_id: str) -> list[dspy.Example]:
        """Labeled sessions for `sopops.ai.programs.calibrate` on `check:<procedure_id>`, from this workspace's
        verdicts and overrides only: every session with a reviewed finding on the procedure. A finding judged wrong
        (`checker_wrong`, or overridden as wrong) is a negative label: its rule isn't `failing`, and `deviated` is the
        result without it; every other finding of the session stays a failure. `wrong` holds the rules judged wrong."""
        with self._db() as db:
            findings = list(db.exec(select(Finding).where(Finding.workspace_id == ws, Finding.procedure_id == procedure_id)))
            labels = _labels(db, ws, findings)
            by_session: dict[str, list[Finding]] = defaultdict(list)
            for f in findings:
                by_session[f.session_id].append(f)
            reviewed = sorted(sid for sid, fs in by_session.items() if any(self.wrong(*labels[f.id]) is not None for f in fs))
            if not reviewed:
                return []
            checks = {pc.session_id: pc for pc in db.exec(select(ProcedureCheck).where(ProcedureCheck.session_id.in_(reviewed), ProcedureCheck.procedure_id == procedure_id))}
            rules: dict[str, list[RuleResult]] = defaultdict(list)
            for r in db.exec(select(RuleResult).where(RuleResult.session_id.in_(reviewed), RuleResult.procedure_id == procedure_id)):
                rules[r.session_id].append(r)
            sessions = {s.id: s for s in db.exec(select(SessionRecord).where(SessionRecord.id.in_(reviewed)))}
        out = []
        for sid in reviewed:
            pc = checks.get(sid)
            if pc is None:
                continue
            wrong = {f.rule_result_id for f in by_session[sid] if self.wrong(*labels[f.id])}
            failing = {f.rule_id for f in by_session[sid] if f.rule_result_id not in wrong}
            result = reviewed_result(pc, rules[sid], wrong)
            wrong_rules = {f.rule_id for f in by_session[sid] if f.rule_result_id in wrong}
            out.append(dspy.Example(turns=sessions[sid].turns, deviated=result == "deviated", failing=failing, wrong=wrong_rules, session_id=sid).with_inputs("turns"))
        return out


def _example(f: Finding, r: RuleResult, at: datetime) -> dict[str, Any]:
    quote = next((str(e.get("quote")) for e in (r.evidence or []) if e.get("quote") and e.get("source", "transcript") == "transcript"), None)
    return {"finding_id": f.id, "session_id": f.session_id, "started_at": _utc(at), "status": r.status, "excerpt": quote,
            "reason": (r.reason or "").split(f" {LENIENT_MARK}")[0] or None, "evidence": r.evidence or [], "suppressed": f.suppressed}
