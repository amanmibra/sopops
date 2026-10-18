"""Insights: what the checks say across sessions. Every number on every Insights page comes from here, computed on the
fly from `sessions`, `procedure_checks`, `rule_results`, `findings` and `patterns`, with one set of definitions:

- **Window**: `[start, end]` on `session.started_at`, both ends inclusive, at most 366 days. The prior window is the
  same length right before it, always derived.
- **Eligible session**: status `checked` or `partial` with a result (its checks ran). Sessions still in progress
  (`received`, `routed`) or `failed` are counted, never rated; `excluded` sessions are counted separately.
- **Session outcome**: the worst result of its procedure checks in scope (only the filtered procedure, if one is
  set): `deviated` = needs attention, `followed` = followed end to end; `partial` and `not_measurable` are neither.
- **Procedure check** (one session x one procedure): measurable when its result is deviated, partial or followed;
  `not_measurable` checks are counted, never rated. Pass rate = followed / measurable.
- **Rule result**: a step passes when `done` and fails when `skipped` or `out_of_order`; a never rule passes when `ok`
  and fails when `violated`. `n` = passes + fails: `not_reached` and `not_applicable` are left out (and shown as
  shares). Warning signs never enter a pass rate: they are `present` (`present` or `unheeded`) or `absent`.
- **Percentages** are whole numbers, rounded half up, always next to their `n`; a zero denominator is null ("—").
  Deltas between rates are in points (the difference of the two whole percentages); deltas between counts are in %.
- **Comparisons** (releases): a 95% Wilson interval per rate; a side with n < MIN_N is `thin` and never flagged;
  `regressed` / `improved` need |delta| >= FLAG_POINTS and intervals that don't overlap.
- Release splits use the release each session ran (`session.release_id`), never dates.
- **Rules** are counted by lineage (`sopops.lineage`): a reworded rule's results, findings and patterns add up with its
  earlier wordings', under the wording sessions ran most recently (`rule_id`, `text`). `lineage_id` is the first
  rule's id, `wording_history` every wording with the release that published it, `reworded_in` the release that
  brought the current one.
- **Thresholds** live here and are returned with every view (`thresholds`), so the UI never hardcodes one: rates
  get a tone, a "most failed" mark or a callout only at n >= MIN_N (HOT_MIN_N for a hot rule).
- **Callouts** are templated from these numbers, no model: a hot rule, a release that moved a procedure, a warning
  sign that drops the pass rate, a rising pattern.
- **Suppressed findings** (a reviewer's verdict or override says they aren't a problem; see `sopops.verdicts`) are
  left out of every count and rate: their rule results aren't judged, and procedure and session results are the ones
  rolled up without them. They're counted on their own (`suppressed`).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, Iterable

from sqlmodel import Session, and_, func, or_, select

from . import lineage
from .orm import Finding, Pattern, ProcedureCheck, RuleResult, SessionRecord, Theme
from .pipeline.rollup import SEVERITY

if TYPE_CHECKING:
    from .store import Store

MAX_WINDOW = timedelta(days=366)
WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90), "365d": timedelta(days=365)}
DEFAULT_WINDOW = "7d"
MIN_N = 10
FLAG_POINTS = 5
WARNING_DROP_POINTS = 10
DAILY_UNTIL = timedelta(days=31)
HOT_MIN_N = 15  # a hot-rule callout needs this many judged results
FAIL_WARN, FAIL_BAD = 8, 15  # fail-rate tones, in %, only at n >= MIN_N
AGENT_MIN_N = 3  # an agent's biggest gap: only rules it ran this many times
RISING_MIN = 5  # a rising pattern: at least this many findings, and at least twice the prior window's
THRESHOLDS = {"min_n": MIN_N, "flag_points": FLAG_POINTS, "warning_drop_points": WARNING_DROP_POINTS, "hot_min_n": HOT_MIN_N,
              "fail_warn": FAIL_WARN, "fail_bad": FAIL_BAD, "agent_min_n": AGENT_MIN_N, "rising_min": RISING_MIN}

ELIGIBLE_STATUSES = ("checked", "partial")
MEASURABLE = ("deviated", "partial", "followed")
STEP_PASS, STEP_FAIL = ("done",), ("skipped", "out_of_order")
NEVER_PASS, NEVER_FAIL = ("ok",), ("violated",)
WARNING_PRESENT = ("present", "unheeded")


# --- small helpers -------------------------------------------------------------------------------------------------


def pct(n: int | float, d: int | float) -> int | None:
    """n / d as a whole percentage, rounded half up; None when d is 0."""
    if not d:
        return None
    return int(Decimal(str(100 * n / d)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def delta_points(cur: int | None, prior: int | None) -> int | None:
    return None if cur is None or prior is None else cur - prior


def delta_pct(cur: int, prior: int) -> int | None:
    """Change in a count, as a whole %; None when the prior is 0."""
    return pct(cur - prior, prior)


def wilson(passes: int, n: int, z: float = 1.96) -> tuple[int, int] | None:
    """95% Wilson score interval as whole percentages (lo, hi); None when n is 0."""
    if n == 0:
        return None
    p = passes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return pct(max(0.0, centre - half), 1) or 0, pct(min(1.0, centre + half), 1) or 0


def rule_pass(kind: str, status: str) -> bool | None:
    """True a pass, False a fail, None not counted (or a warning sign)."""
    if kind == "step":
        return True if status in STEP_PASS else False if status in STEP_FAIL else None
    if kind == "never":
        return True if status in NEVER_PASS else False if status in NEVER_FAIL else None
    return None


def compare_flag(a_pass: int, a_n: int, b_pass: int, b_n: int) -> str:
    """thin | regressed | improved | steady, for B against A."""
    if a_n < MIN_N or b_n < MIN_N:
        return "thin"
    ra, rb = pct(a_pass, a_n), pct(b_pass, b_n)
    ia, ib = wilson(a_pass, a_n), wilson(b_pass, b_n)
    d = (rb or 0) - (ra or 0)
    apart = ia is not None and ib is not None and (ib[1] < ia[0] or ib[0] > ia[1])
    if d <= -FLAG_POINTS and apart:
        return "regressed"
    if d >= FLAG_POINTS and apart:
        return "improved"
    return "steady"


def fail_tone(fail_rate: int | None, n: int) -> str | None:
    """bad | warn | None for a fail rate; None below MIN_N, so a handful of results never reads as a problem."""
    if fail_rate is None or n < MIN_N:
        return None
    return "bad" if fail_rate >= FAIL_BAD else "warn" if fail_rate >= FAIL_WARN else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def _utc(at: datetime | None) -> datetime | None:
    if at is None:
        return None
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)


# --- filters ---------------------------------------------------------------------------------------------------------


class BadFilter(ValueError):
    pass


@dataclass(frozen=True)
class Filters:
    workspace: str
    start: datetime
    end: datetime
    agent: str | None = None
    procedure: str | None = None
    releases: tuple[int, ...] = ()
    channel: str | None = None

    @classmethod
    def make(cls, workspace: str, window: str | None = None, start: datetime | None = None, end: datetime | None = None, agent: str | None = None,
             procedure: str | None = None, release: Iterable[int] | None = None, channel: str | None = None, now: datetime | None = None) -> Filters:
        """`window` (24h, 7d, 30d, 90d, 365d; default 7d) ending now, or `start` (and optionally `end`)."""
        now = _utc(now) or datetime.now(timezone.utc)
        if start is not None:
            s, e = _utc(start), _utc(end) or now
        else:
            if window and window not in WINDOWS:
                raise BadFilter(f"window must be one of {', '.join(WINDOWS)}")
            e = _utc(end) or now
            s = e - WINDOWS[window or DEFAULT_WINDOW]
        assert s is not None and e is not None
        if e < s:
            raise BadFilter("end is before start")
        if e - s > MAX_WINDOW:
            raise BadFilter("the window can be at most 366 days")
        return cls(workspace, s, e, agent or None, procedure or None, tuple(sorted(set(release or ()))), channel or None)

    def prior(self) -> Filters:
        span = self.end - self.start
        return Filters(self.workspace, self.start - span - timedelta(microseconds=1), self.start - timedelta(microseconds=1), self.agent, self.procedure, self.releases, self.channel)

    def with_(self, **kw: Any) -> Filters:
        return Filters(**{**self.__dict__, **kw})

    def sessions(self, q, S=SessionRecord, window: bool = True):
        """Apply the session-side filters (workspace, window, agent, release, channel) to a query joined to sessions."""
        q = q.where(S.workspace_id == self.workspace)
        if window:
            q = q.where(S.started_at >= self.start, S.started_at <= self.end)
        if self.agent:
            q = q.where(or_(S.agent_id == self.agent, S.agent_ref == self.agent))
        if self.releases:
            q = q.where(S.release_id.in_(self.releases))
        if self.channel:
            q = q.where(S.channel == self.channel)
        return q

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "agent": self.agent, "procedure": self.procedure, "releases": list(self.releases), "channel": self.channel}


ELIGIBLE = and_(SessionRecord.status.in_(ELIGIBLE_STATUSES), SessionRecord.result.is_not(None))
# rule results that aren't a suppressed finding
UNSUPPRESSED = RuleResult.id.not_in(select(Finding.rule_result_id).where(Finding.suppressed.is_(True)))


# --- the queries -------------------------------------------------------------------------------------------------------


class Insights:
    def __init__(self, store: Store):
        self.store = store

    def _db(self) -> Session:
        return Session(self.store.engine)

    # --- building blocks ---

    def _checks(self, db: Session, f: Filters) -> list[tuple]:
        """(session_id, procedure_id, procedure_name, result, goal_reached, release_id, started_at) for checked procedure
        checks of eligible sessions."""
        S, PC = SessionRecord, ProcedureCheck
        q = select(PC.session_id, PC.procedure_id, PC.procedure_name, PC.result, PC.goal_reached, S.release_id, S.started_at).join(S, S.id == PC.session_id)
        q = f.sessions(q).where(ELIGIBLE, PC.result.is_not(None))
        if f.procedure:
            q = q.where(PC.procedure_id == f.procedure)
        return list(db.exec(q))

    def _counts(self, db: Session, f: Filters) -> dict[str, int]:
        """Sessions by status in the window, with the procedure filter meaning "the router considered it"."""
        S, PC = SessionRecord, ProcedureCheck
        q = f.sessions(select(S.status, func.count(S.id)))
        if f.procedure:
            q = q.where(S.id.in_(select(PC.session_id).where(PC.procedure_id == f.procedure, PC.applicable.is_(True))))
        return dict(db.exec(q.group_by(S.status)).all())

    def _tiles(self, db: Session, f: Filters) -> dict[str, Any]:
        checks = self._checks(db, f)
        outcome: dict[str, str] = {}
        for sid, _pid, _name, result, *_ in checks:
            if sid not in outcome or SEVERITY[result] > SEVERITY[outcome[sid]]:
                outcome[sid] = result
        oc = Counter(outcome.values())
        measurable = [c for c in checks if c[3] in MEASURABLE]
        followed = sum(1 for c in measurable if c[3] == "followed")
        goal_n = [c for c in measurable if c[4] is not None]
        status = self._counts(db, f)
        excluded = status.get("excluded", 0)
        if f.procedure:  # excluded for this procedure: the router said the session ended before it started or finished
            S, PC = SessionRecord, ProcedureCheck
            excluded = db.exec(f.sessions(select(func.count(func.distinct(PC.session_id))).join(S, S.id == PC.session_id)).where(PC.procedure_id == f.procedure, PC.excluded_reason.is_not(None))).one()
        checked = len(outcome)
        rule_passes = rule_n = nevers = 0
        for _pid, _rid, kind, _index, st, n in self._rule_rows(db, f):
            ok = rule_pass(kind, st)
            if ok is not None:
                rule_n += n
                rule_passes += n if ok else 0
            if kind == "never" and st in NEVER_FAIL:
                nevers += n
        S, R = SessionRecord, RuleResult
        nq = f.sessions(select(func.count(func.distinct(R.session_id))).join(S, S.id == R.session_id)).where(ELIGIBLE, R.kind == "never", R.status.in_(NEVER_FAIL), UNSUPPRESSED)
        if f.procedure:
            nq = nq.where(R.procedure_id == f.procedure)
        F = Finding
        sq = f.sessions(select(func.count(F.id)).join(S, S.id == F.session_id)).where(ELIGIBLE, F.suppressed.is_(True))
        if f.procedure:
            sq = sq.where(F.procedure_id == f.procedure)
        return {
            "sessions_checked": checked,
            "followed": oc.get("followed", 0),
            "need_attention": oc.get("deviated", 0),
            "partial": oc.get("partial", 0),
            "not_measurable": oc.get("not_measurable", 0),
            "excluded": excluded,
            "in_progress": status.get("received", 0) + status.get("routed", 0),
            "failed": status.get("failed", 0),
            "followed_pct": pct(oc.get("followed", 0), checked),
            "need_attention_pct": pct(oc.get("deviated", 0), checked),
            "checks": len(checks),
            "measurable_checks": len(measurable),
            "pass_rate": pct(followed, len(measurable)),
            "goal_n": len(goal_n),
            "goal_rate": pct(sum(1 for c in goal_n if c[4]), len(goal_n)),
            "rule_n": rule_n,
            "rule_passes": rule_passes,
            "rule_pass_rate": pct(rule_passes, rule_n),
            "never_violations": nevers,
            "never_sessions": db.exec(nq).one(),
            "suppressed": db.exec(sq).one(),
        }

    def tiles(self, db: Session, f: Filters) -> dict[str, Any]:
        cur, prior = self._tiles(db, f), self._tiles(db, f.prior())
        rates = ("followed_pct", "need_attention_pct", "pass_rate", "goal_rate", "rule_pass_rate")
        counts = ("sessions_checked", "followed", "need_attention", "excluded", "never_violations")
        return {
            "current": cur,
            "prior": prior,
            "delta_points": {k: delta_points(cur[k], prior[k]) for k in rates},
            "delta_pct": {k: delta_pct(cur[k], prior[k]) for k in counts},
        }

    def _rule_rows(self, db: Session, f: Filters, procedure_id: str | None = None, by: str | None = None) -> list[tuple]:
        """(procedure_id, lineage_id, kind, index, status, [group], count) for rule results of eligible sessions: a
        reworded rule's rows come under its lineage id (one row per wording; sum them)."""
        S, R = SessionRecord, RuleResult
        group = {"release": S.release_id, "agent": func.coalesce(S.agent_id, S.agent_ref)}.get(by or "")
        cols = [R.procedure_id, R.rule_id, R.kind, R.index, R.status, *([group] if group is not None else []), func.count(R.id)]
        q = f.sessions(select(*cols).join(S, S.id == R.session_id)).where(ELIGIBLE, UNSUPPRESSED)
        pid = procedure_id or f.procedure
        if pid:
            q = q.where(R.procedure_id == pid)
        lins = lineage.lineages(db, f.workspace, pid)
        return [(r[0], lins.get((r[0], r[1]), r[1]), *r[2:]) for r in db.exec(q.group_by(*cols[:-1])).all()]

    @staticmethod
    def _rule_stats(rows: Iterable[tuple]) -> dict[str, Any]:
        """Counts for one rule (rows: (status, count))."""
        c = Counter()
        kind = None
        for k, status, n in rows:
            kind = k
            c[status] += n
        total = sum(c.values())
        if kind == "warning":
            present = c["present"] + c["unheeded"]
            return {"total": total, "counts": dict(c), "n": total, "passes": None, "fails": None, "pass_rate": None, "fail_rate": None,
                    "skip_rate": None, "present": present, "present_rate": pct(present, total), "unheeded": c["unheeded"], "not_counted": 0, "not_counted_pct": None}
        passes = sum(n for s, n in c.items() if rule_pass(kind or "step", s) is True)
        fails = sum(n for s, n in c.items() if rule_pass(kind or "step", s) is False)
        n = passes + fails
        nc = c["not_reached"] + c["not_applicable"]
        return {"total": total, "counts": dict(c), "n": n, "passes": passes, "fails": fails, "pass_rate": pct(passes, n), "fail_rate": pct(fails, n),
                "skip_rate": pct(c["skipped"], n) if kind == "step" else None, "present": None, "present_rate": None, "unheeded": None,
                "not_counted": nc, "not_counted_pct": pct(nc, total)}

    def _rule_texts(self, db: Session, ws: str, procedure_id: str | None) -> dict[tuple[str, str], dict[str, Any]]:
        """{(procedure id, lineage id): the rule as sessions ran it most recently (`rule_id`, kind, index, text), how
        many wordings it has had, each with the release that published it (`history`), and `reworded_in`}."""
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for key, group in lineage.wordings(db, ws, procedure_id).items():
            cur = lineage.current(group)
            out[key] = {"rule_id": cur.rule_id, "kind": cur.kind, "index": cur.index, "text": cur.text, "text_hash": cur.text_hash, "wordings": len(group),
                        "history": [w.out() for w in group], "reworded_in": cur.from_release if len(group) > 1 else None}
        return out

    def _pattern_window_counts(self, db: Session, f: Filters, procedure_id: str | None = None, suppressed: bool = False) -> dict[str, tuple[int, int]]:
        """{pattern_id: (findings, sessions)} for findings of sessions in the window (only suppressed ones with `suppressed`)."""
        S, F = SessionRecord, Finding
        q = f.sessions(select(F.pattern_id, func.count(F.id), func.count(func.distinct(F.session_id))).join(S, S.id == F.session_id))
        q = q.where(ELIGIBLE, F.pattern_id.is_not(None), F.suppressed.is_(suppressed))
        pid = procedure_id or f.procedure
        if pid:
            q = q.where(F.procedure_id == pid)
        return {p: (n, s) for p, n, s in db.exec(q.group_by(F.pattern_id)).all()}

    def _patterns(self, db: Session, f: Filters, procedure_id: str | None, rule_id: str | None = None) -> list[dict[str, Any]]:
        """Rule patterns with their sub-patterns, counts in the window (all-time counts alongside)."""
        P = Pattern
        q = select(P).where(P.workspace_id == f.workspace)
        if procedure_id:
            q = q.where(P.procedure_id == procedure_id)
        if rule_id:  # any wording of the rule: its pattern is keyed by the lineage id
            q = q.where(P.rule_id.in_(lineage.expand(db, f.workspace, rule_id, procedure_id)[0]))
        pats = list(db.exec(q))
        counts = self._pattern_window_counts(db, f, procedure_id)
        hidden = self._pattern_window_counts(db, f, procedure_id, suppressed=True)
        theme_names = dict(db.exec(select(Theme.id, Theme.name).where(Theme.workspace_id == f.workspace)).all())
        children: dict[str, list[Pattern]] = defaultdict(list)
        for p in pats:
            if p.parent_id:
                children[p.parent_id].append(p)
        out = []
        for p in pats:
            if p.kind != "rule":
                continue
            subs = []
            for c in sorted(children.get(p.id, []), key=lambda c: (c.kind == "other", -counts.get(c.id, (0, 0))[0], c.name)):
                n, s = counts.get(c.id, (0, 0))
                if c.status != "active" and n == 0:
                    continue
                subs.append({**_pattern_out(c, n, s), "theme_name": theme_names.get(c.theme_id or ""), "suppressed": hidden.get(c.id, (0, 0))[0]})
            own = counts.get(p.id, (0, 0))
            n = own[0] + sum(x["findings"] for x in subs)
            s = own[1] + sum(x["sessions"] for x in subs)  # a session has at most one finding per rule
            gone = hidden.get(p.id, (0, 0))[0] + sum(hidden.get(c.id, (0, 0))[0] for c in children.get(p.id, []))
            out.append({**_pattern_out(p, n, s), "theme_name": theme_names.get(p.theme_id or ""), "suppressed": gone, "sub_patterns": subs})
        return out

    # --- views ---

    def overview(self, f: Filters) -> dict[str, Any]:
        with self._db() as db:
            tiles = self.tiles(db, f)
            trend = self._trend(db, f)
            board = self._scoreboard(db, f)
            problems = self._problems(db, f)
            agents = self._agents(db, f)
            callouts = []
            for row in board:
                pf = f.with_(procedure=row["procedure_id"])
                ladder = self._ladder(db, pf, row["procedure_id"])
                mine = self._callouts(db, pf, row["procedure_id"], row["procedure_name"], ladder, self._warning_impact(db, pf, row["procedure_id"], ladder))
                row["callouts"] = len(mine)
                callouts += mine
        return {"filters": f.as_dict(), "thresholds": THRESHOLDS, "tiles": tiles, "trend": trend, "scoreboard": board, "problems": problems,
                "agents": agents, "callouts": callouts}

    PROBLEMS = (
        ("skipped", "Step skipped", ("skipped",), "step"),
        ("never", "Never rule broken", NEVER_FAIL, "never"),
        ("out_of_order", "Step out of order", ("out_of_order",), "step"),
        ("not_reached", "Ended before a step", ("not_reached",), "step"),
        ("unheeded", "Warning sign unheeded", ("unheeded",), "warning"),
    )

    def _problems(self, db: Session, f: Filters) -> list[dict[str, Any]]:
        """Measurable procedure checks with each kind of problem; one check can have several, so they overlap."""
        S, R, PC = SessionRecord, RuleResult, ProcedureCheck
        q = f.sessions(select(R.session_id, R.procedure_id, R.kind, R.status).join(S, S.id == R.session_id)
                       .join(PC, and_(PC.session_id == R.session_id, PC.procedure_id == R.procedure_id)))
        q = q.where(ELIGIBLE, UNSUPPRESSED, PC.result.in_(MEASURABLE), R.status.in_(("skipped", "out_of_order", "not_reached", "unheeded", *NEVER_FAIL))).distinct()
        if f.procedure:
            q = q.where(R.procedure_id == f.procedure)
        hits: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for sid, pid, kind, status in db.exec(q).all():
            for key, _label, statuses, k in self.PROBLEMS:
                if kind == k and status in statuses:
                    hits[key].add((sid, pid))
        of = sum(1 for c in self._checks(db, f) if c[3] in MEASURABLE)
        return [{"type": key, "label": label, "checks": len(hits[key]), "of": of, "pct": pct(len(hits[key]), of), "statuses": list(statuses), "kind": k}
                for key, label, statuses, k in self.PROBLEMS]

    def _agents(self, db: Session, f: Filters) -> list[dict[str, Any]]:
        """Each agent's procedure checks: pass rate (current and prior), the rule it fails most (among rules it ran at
        least AGENT_MIN_N times), and the median session length."""
        S, PC = SessionRecord, ProcedureCheck
        agent = func.coalesce(S.agent_id, S.agent_ref)

        def load(ff: Filters):
            q = ff.sessions(select(agent, PC.session_id, PC.result, S.started_at, S.ended_at).join(S, S.id == PC.session_id)).where(ELIGIBLE, PC.result.is_not(None))
            if ff.procedure:
                q = q.where(PC.procedure_id == ff.procedure)
            return list(db.exec(q).all())

        cur, prior = load(f), load(f.prior())
        by: dict[Any, list] = defaultdict(list)
        for row in cur:
            by[row[0]].append(row)
        prior_by: dict[Any, Counter] = defaultdict(Counter)
        for a, _sid, result, *_ in prior:
            if result in MEASURABLE:
                prior_by[a]["n"] += 1
                prior_by[a]["followed"] += result == "followed"
        rules: dict[tuple[Any, str, str], list[tuple]] = defaultdict(list)
        for pid, rid, kind, _i, status, a, n in self._rule_rows(db, f, by="agent"):
            rules[(a, pid, rid)].append((kind, status, n))
        texts = self._rule_texts(db, f.workspace, f.procedure)
        out = []
        for a, rows in by.items():
            m = [r for r in rows if r[2] in MEASURABLE]
            fol = sum(r[2] == "followed" for r in m)
            pn, pf_ = prior_by[a]["n"], prior_by[a]["followed"]
            rate, prate = pct(fol, len(m)), pct(pf_, pn)
            gap = None
            for (a2, pid, rid), rr in rules.items():
                if a2 != a:
                    continue
                st = self._rule_stats(rr)
                if st["n"] >= AGENT_MIN_N and st["fails"] and (gap is None or (st["fail_rate"] or 0) > (gap["fail_rate"] or 0)):
                    t = texts.get((pid, rid), {})
                    gap = {"procedure_id": pid, "rule_id": t.get("rule_id", rid), "kind": t.get("kind"), "index": t.get("index"), "text": t.get("text"), "n": st["n"], "fails": st["fails"],
                           "fail_rate": st["fail_rate"], "tone": fail_tone(st["fail_rate"], st["n"])}
            durations = [(_utc(e) - _utc(s)).total_seconds() for *_x, s, e in {r[1]: r for r in rows}.values() if e is not None]
            out.append({"agent": a, "sessions": len({r[1] for r in rows}), "checks": len(rows), "measurable": len(m), "followed": fol,
                        "partial": sum(r[2] == "partial" for r in m), "deviated": sum(r[2] == "deviated" for r in m), "pass_rate": rate,
                        "prior_pass_rate": prate, "prior_measurable": pn, "delta_points": delta_points(rate, prate) if len(m) >= MIN_N and pn >= MIN_N else None,
                        "thin": len(m) < MIN_N, "top_gap": gap, "median_duration_s": _median(durations)})
        out.sort(key=lambda r: (-r["measurable"], str(r["agent"])))
        return out

    def _callouts(self, db: Session, f: Filters, procedure_id: str, name: str, ladder: list[dict[str, Any]], impact: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """What stands out about one procedure in the window, each with the sessions behind it (`drill`)."""
        out: list[dict[str, Any]] = []
        # hot rule: the most-failed step or never rule, judged at least HOT_MIN_N times, failing at least FAIL_BAD %
        hot = [r for r in ladder if r["kind"] != "warning" and r["n"] >= HOT_MIN_N and (r["fail_rate"] or 0) >= FAIL_BAD]
        if hot:
            r = max(hot, key=lambda r: (r["fail_rate"], r["n"]))
            label = f"{'Step' if r['kind'] == 'step' else 'Never rule'} {r['index']}"
            out.append({"id": f"hot:{procedure_id}:{r['rule_id']}", "kind": "hot_rule", "tone": "bad", "procedure_id": procedure_id, "procedure_name": name,
                        "rule_id": r["rule_id"], "title": f"{label} of {name} fails {r['fail_rate']}% of the time",
                        "body": f"{r['fails']} of {r['n']} sessions: “{r['text']}”",
                        "drill": {"procedure": procedure_id, "rule": r["rule_id"], "rule_status": list(STEP_FAIL if r["kind"] == "step" else NEVER_FAIL)}})
        # release effect: the latest release in the window against the one before, by the release each session ran
        per: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for _sid, _pid, _n, result, _g, rel, _at in self._checks(db, f.with_(procedure=procedure_id)):
            if rel is not None and result in MEASURABLE:
                per[rel][1] += 1
                per[rel][0] += result == "followed"
        if len(per) >= 2:
            a, b = sorted(per)[-2:]
            flag = compare_flag(per[a][0], per[a][1], per[b][0], per[b][1])
            if flag in ("improved", "regressed"):
                ra, rb = pct(*per[a]), pct(*per[b])
                verb = "lifted" if flag == "improved" else "dropped"
                out.append({"id": f"release:{procedure_id}:{a}:{b}", "kind": "release", "tone": "good" if flag == "improved" else "bad", "procedure_id": procedure_id,
                            "procedure_name": name, "rule_id": None, "title": f"Release #{b} {verb} {name} from {ra}% to {rb}%",
                            "body": f"Followed end to end on {per[b][0]} of {per[b][1]} sessions on #{b}, against {per[a][0]} of {per[a][1]} on #{a}. The two releases' 95% ranges don't overlap, so the change is unlikely to be chance.",
                            "drill": {"procedure": procedure_id, "release": b}})
        # warning signs that drop the pass rate
        for w in impact:
            if w["flag"]:
                out.append({"id": f"warning:{procedure_id}:{w['rule_id']}", "kind": "warning", "tone": "bad", "procedure_id": procedure_id, "procedure_name": name,
                            "rule_id": w["rule_id"], "title": f"{name} passes less often when warning sign {w['index']} comes up",
                            "body": f"Pass rate {w['present_pass_rate']}% of {w['present_n']} checks with the sign, against {w['absent_pass_rate']}% of {w['absent_n']} without it: “{w['text']}”",
                            "drill": {"procedure": procedure_id, "rule": w["rule_id"], "rule_status": list(WARNING_PRESENT)}})
        # rising patterns: at least RISING_MIN findings and at least twice the prior window's. A rule pattern counts its
        # whole family (findings wait in its "other" until they're clustered); a named sub-pattern counts on its own.
        now, before = self._pattern_window_counts(db, f, procedure_id), self._pattern_window_counts(db, f.prior(), procedure_id)
        pats = list(db.exec(select(Pattern).where(Pattern.workspace_id == f.workspace, Pattern.procedure_id == procedure_id)).all())
        family = {p.id: [p.id] + [c.id for c in pats if c.parent_id == p.id] for p in pats if p.kind == "rule"}
        count = lambda counts, ids: sum(counts.get(i, (0, 0))[0] for i in ids)  # noqa: E731
        rising = [(p, count(now, family[p.id] if p.kind == "rule" else [p.id]), count(before, family[p.id] if p.kind == "rule" else [p.id]))
                  for p in pats if p.kind in ("rule", "sub")]
        fired = set()
        prior_checks = sum(1 for c in self._checks(db, f.prior().with_(procedure=procedure_id)) if c[3] in MEASURABLE)
        if prior_checks < MIN_N:  # nothing to rise from: a window before any sessions would make every pattern "rising"
            rising = []
        for p, n, was in sorted(rising, key=lambda x: (x[0].kind != "rule", -x[1])):
            if n < RISING_MIN or n < 2 * was or (p.parent_id and p.parent_id in fired):
                continue
            fired.add(p.id)
            out.append({"id": f"rising:{p.id}", "kind": "rising_pattern", "tone": "bad", "procedure_id": procedure_id, "procedure_name": name, "rule_id": p.rule_id,
                        "title": f"“{p.name}” is rising on {name}", "body": f"{n} findings in this time range, up from {was} in the period before ({prior_checks} checks then).",
                        "drill": {"pattern": p.id}})
        return out

    @staticmethod
    def _buckets(f: Filters) -> tuple[str, list[datetime], Any]:
        """The trend's buckets for the window: days up to 31 days, else weeks (Monday), in UTC. Every chart on a view
        uses these, so nothing ignores the selected window."""
        daily = f.end - f.start <= DAILY_UNTIL

        def bucket(at: datetime) -> datetime:
            d = _utc(at).replace(hour=0, minute=0, second=0, microsecond=0)
            return d if daily else d - timedelta(days=d.weekday())

        starts, b = [], bucket(f.start)
        while b <= f.end:
            starts.append(b)
            b += timedelta(days=1 if daily else 7)
        return ("day" if daily else "week"), starts, bucket

    def _trend(self, db: Session, f: Filters) -> dict[str, Any]:
        unit, starts, bucket = self._buckets(f)
        checks = self._checks(db, f)
        outcome: dict[str, tuple[str, datetime]] = {}
        for sid, _pid, _name, result, _goal, _rel, started in checks:
            if sid not in outcome or SEVERITY[result] > SEVERITY[outcome[sid][0]]:
                outcome[sid] = (result, _utc(started))
        buckets: dict[datetime, Counter] = {s: Counter() for s in starts}
        for result, at in outcome.values():
            buckets.setdefault(bucket(at), Counter())[result] += 1
        step = timedelta(days=1 if unit == "day" else 7)
        rows = []
        for start in sorted(buckets):
            c = buckets[start]
            m = c["followed"] + c["partial"] + c["deviated"]
            rows.append({"start": start, "end": min(start + step, f.end), "followed": c["followed"], "partial": c["partial"], "deviated": c["deviated"],
                         "not_measurable": c["not_measurable"], "pass_rate": pct(c["followed"], m), "n": m})
        S = SessionRecord
        first = db.exec(f.sessions(select(S.release_id, func.min(S.started_at)).where(S.release_id.is_not(None))).group_by(S.release_id)).all()
        markers = sorted(({"release": r, "first_session": _utc(at)} for r, at in first), key=lambda m: m["first_session"])
        return {"bucket": unit, "buckets": rows, "releases": markers}

    def _spark(self, f: Filters, checks: list[tuple]) -> list[dict[str, Any]]:
        """Pass rate per trend bucket for these procedure checks (the window's buckets, not a fixed span)."""
        _unit, starts, bucket = self._buckets(f)
        c: dict[datetime, list[int]] = {s: [0, 0] for s in starts}
        for _sid, _pid, _name, result, _goal, _rel, started in checks:
            if result in MEASURABLE:
                x = c.setdefault(bucket(started), [0, 0])
                x[1] += 1
                x[0] += result == "followed"
        return [{"start": s, "n": c[s][1], "pass_rate": pct(c[s][0], c[s][1])} for s in sorted(c)]

    def _scoreboard(self, db: Session, f: Filters) -> list[dict[str, Any]]:
        cur, prior = self._checks(db, f), self._checks(db, f.prior())
        by_proc: dict[str, list[tuple]] = defaultdict(list)
        names: dict[str, str] = {}
        for c in cur:
            by_proc[c[1]].append(c)
            names[c[1]] = c[2] or c[1]
        prior_by: dict[str, list[tuple]] = defaultdict(list)
        for c in prior:
            prior_by[c[1]].append(c)
        rules = self._rule_rows(db, f)
        per_rule: dict[tuple[str, str], list[tuple]] = defaultdict(list)
        for pid, rid, kind, index, status, n in rules:
            per_rule[(pid, rid)].append((kind, status, n))
        texts = self._rule_texts(db, f.workspace, f.procedure)
        counts = self._pattern_window_counts(db, f)
        hidden = self._pattern_window_counts(db, f, suppressed=True)
        pats = list(db.exec(select(Pattern).where(Pattern.workspace_id == f.workspace)))
        excluded = dict(db.exec(f.sessions(select(ProcedureCheck.procedure_id, func.count(ProcedureCheck.id)).join(SessionRecord, SessionRecord.id == ProcedureCheck.session_id))
                                .where(ProcedureCheck.excluded_reason.is_not(None)).group_by(ProcedureCheck.procedure_id)).all())
        out = []
        for pid in sorted(set(by_proc) | set(prior_by)):
            rows = by_proc.get(pid, [])
            m = [c for c in rows if c[3] in MEASURABLE]
            pm = [c for c in prior_by.get(pid, []) if c[3] in MEASURABLE]
            rate, prate = pct(sum(c[3] == "followed" for c in m), len(m)), pct(sum(c[3] == "followed" for c in pm), len(pm))
            worst = []
            nevers = 0
            for (p2, rid), rr in per_rule.items():
                if p2 != pid:
                    continue
                st = self._rule_stats(rr)
                t = texts.get((pid, rid), {})
                if t.get("kind") == "never":
                    nevers += st["fails"] or 0
                if st["n"] >= MIN_N and st["fails"]:  # a rule judged a handful of times is never "most failed"
                    worst.append({"rule_id": t.get("rule_id", rid), "kind": t.get("kind"), "index": t.get("index"), "text": t.get("text"), "n": st["n"], "fails": st["fails"],
                                  "fail_rate": st["fail_rate"], "tone": fail_tone(st["fail_rate"], st["n"])})
            worst.sort(key=lambda r: (-(r["fail_rate"] or 0), -r["n"]))
            open_patterns = sum(1 for p in pats if p.procedure_id == pid and counts.get(p.id, (0, 0))[0] > 0)  # leaves with findings in the window
            suppressed = sum(hidden.get(p.id, (0, 0))[0] for p in pats if p.procedure_id == pid)
            out.append({
                "procedure_id": pid, "procedure_name": names.get(pid) or (prior_by[pid][0][2] if prior_by.get(pid) else pid),
                "checks": len(rows), "measurable": len(m), "followed": sum(c[3] == "followed" for c in m), "partial": sum(c[3] == "partial" for c in m),
                "deviated": sum(c[3] == "deviated" for c in m), "not_measurable": len(rows) - len(m), "excluded": excluded.get(pid, 0),
                "pass_rate": rate, "prior_pass_rate": prate, "prior_measurable": len(pm), "delta_points": delta_points(rate, prate),
                "top_failing_rules": worst[:2], "never_violations": nevers, "open_patterns": open_patterns, "suppressed": suppressed,
                "rules": sum(1 for (p2, _r) in texts if p2 == pid), "spark": self._spark(f, rows), "callouts": 0,
            })
        out.sort(key=lambda r: (r["pass_rate"] if r["pass_rate"] is not None else 101, -r["deviated"]))
        return out

    def procedure(self, f: Filters, procedure_id: str) -> dict[str, Any]:
        f = f.with_(procedure=procedure_id)
        with self._db() as db:
            tiles = self.tiles(db, f)
            checks = self._checks(db, f)
            name = next((c[2] for c in checks if c[2]), None) or db.exec(select(ProcedureCheck.procedure_name).where(ProcedureCheck.workspace_id == f.workspace, ProcedureCheck.procedure_id == procedure_id).limit(1)).first() or procedure_id
            ladder = self._ladder(db, f, procedure_id)
            split = self._release_split(db, f, procedure_id, ladder)
            impact = self._warning_impact(db, f, procedure_id, ladder)
            themes = self._themes(db, f, procedure_id)
            for th in themes:
                th.pop("_sessions", None)
            trend = self._trend(db, f)
            problems = self._problems(db, f)
            agents = self._agents(db, f)
            callouts = self._callouts(db, f, procedure_id, name, ladder, impact)
        return {"filters": f.as_dict(), "thresholds": THRESHOLDS, "procedure_id": procedure_id, "procedure_name": name, "tiles": tiles, "trend": trend, "rules": ladder,
                "release_split": split, "warning_impact": impact, "themes": themes, "problems": problems, "agents": agents, "callouts": callouts}

    def _ladder(self, db: Session, f: Filters, procedure_id: str) -> list[dict[str, Any]]:
        per: dict[str, list[tuple]] = defaultdict(list)
        for _pid, rid, kind, _index, status, n in self._rule_rows(db, f, procedure_id):
            per[rid].append((kind, status, n))
        texts = self._rule_texts(db, f.workspace, procedure_id)
        counts = self._pattern_window_counts(db, f, procedure_id)
        hidden = self._pattern_window_counts(db, f, procedure_id, suppressed=True)
        pats = list(db.exec(select(Pattern).where(Pattern.workspace_id == f.workspace, Pattern.procedure_id == procedure_id)))
        rows = []
        order = {"step": 0, "never": 1, "warning": 2}
        for (pid, rid), t in texts.items():
            st = self._rule_stats(per.get(rid, []))
            mine = [p for p in pats if p.rule_id == rid]
            rule_pat = next((p for p in mine if p.kind == "rule"), None)
            n_pat = sum(1 for p in mine if p.kind == "sub" and p.status == "active" and counts.get(p.id, (0, 0))[0] > 0)
            findings = sum(counts.get(p.id, (0, 0))[0] for p in mine)
            suppressed = sum(hidden.get(p.id, (0, 0))[0] for p in mine)
            c = st["counts"]
            judged = st["total"] - c.get("not_applicable", 0)
            rows.append({"rule_id": t["rule_id"], "lineage_id": rid, "kind": t["kind"], "index": t["index"], "text": t["text"], "wordings": t["wordings"],
                         "wording_history": t["history"], "reworded_in": t["reworded_in"], **st,
                         "findings": findings, "suppressed": suppressed, "pattern_id": rule_pat.id if rule_pat else None, "patterns": n_pat,
                         "reached_pct": pct(judged - c.get("not_reached", 0), judged) if t["kind"] == "step" else None,
                         "tone": fail_tone(st["fail_rate"], st["n"]), "most_failed": False})
        rows.sort(key=lambda r: (order.get(r["kind"], 3), r["index"]))
        rated = [r for r in rows if r["kind"] != "warning" and r["n"] >= MIN_N and r["fails"]]
        if rated:
            max(rated, key=lambda r: (r["fail_rate"], r["n"]))["most_failed"] = True
        return rows

    def _release_split(self, db: Session, f: Filters, procedure_id: str, ladder: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Previous vs latest release seen in the window, per rule (by the release each session ran)."""
        per: dict[tuple[str, int], list[tuple]] = defaultdict(list)
        releases: set[int] = set()
        for _pid, rid, kind, _index, status, rel, n in self._rule_rows(db, f, procedure_id, by="release"):
            if rel is None:
                continue
            per[(rid, rel)].append((kind, status, n))
            releases.add(rel)
        if len(releases) < 2:
            return None
        b, a = sorted(releases)[-1], sorted(releases)[-2]
        rows = []
        for r in ladder:
            if r["kind"] == "warning":
                continue
            sa, sb = self._rule_stats(per.get((r["lineage_id"], a), [])), self._rule_stats(per.get((r["lineage_id"], b), []))
            ok = sa["n"] >= MIN_N and sb["n"] >= MIN_N
            rows.append({"rule_id": r["rule_id"], "a_n": sa["n"], "a_pass_rate": sa["pass_rate"], "b_n": sb["n"], "b_pass_rate": sb["pass_rate"],
                         "delta_points": delta_points(sb["pass_rate"], sa["pass_rate"]) if ok else None,
                         "flag": compare_flag(sa["passes"] or 0, sa["n"], sb["passes"] or 0, sb["n"])})
        return {"a": a, "b": b, "min_n": MIN_N, "rules": rows}

    def _warning_impact(self, db: Session, f: Filters, procedure_id: str, ladder: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Procedure pass rate with each warning sign present vs absent, over checks that judged the sign."""
        S, R, PC = SessionRecord, RuleResult, ProcedureCheck
        warnings = [r for r in ladder if r["kind"] == "warning"]
        if not warnings:
            return []
        q = f.sessions(select(R.rule_id, R.status, PC.result, func.count(R.id)).join(S, S.id == R.session_id).join(PC, and_(PC.session_id == R.session_id, PC.procedure_id == R.procedure_id)))
        q = q.where(ELIGIBLE, UNSUPPRESSED, R.procedure_id == procedure_id, R.kind == "warning", PC.result.in_(MEASURABLE)).group_by(R.rule_id, R.status, PC.result)
        c: dict[str, Counter] = defaultdict(Counter)
        lins = lineage.lineages(db, f.workspace, procedure_id)
        for rid, status, result, n in db.exec(q).all():
            side = "present" if status in WARNING_PRESENT else "absent"
            lid = lins.get((procedure_id, rid), rid)
            c[lid][(side, "n")] += n
            if result == "followed":
                c[lid][(side, "followed")] += n
        out = []
        for w in warnings:
            x = c.get(w["lineage_id"], Counter())
            pn, an = x[("present", "n")], x[("absent", "n")]
            pr, ar = pct(x[("present", "followed")], pn), pct(x[("absent", "followed")], an)
            d = delta_points(pr, ar) if pn >= MIN_N and an >= MIN_N else None
            out.append({"rule_id": w["rule_id"], "index": w["index"], "text": w["text"], "present_n": pn, "present_pass_rate": pr, "absent_n": an,
                        "absent_pass_rate": ar, "delta_points": d, "flag": d is not None and d <= -WARNING_DROP_POINTS, "unheeded": w.get("unheeded") or 0})
        return out

    def _themes(self, db: Session, f: Filters, procedure_id: str) -> list[dict[str, Any]]:
        pats = self._patterns(db, f, procedure_id)
        themes = list(db.exec(select(Theme).where(Theme.workspace_id == f.workspace, Theme.procedure_id == procedure_id).order_by(Theme.position)))
        texts = self._rule_texts(db, f.workspace, procedure_id)
        for p in pats:
            t = texts.get((procedure_id, p["rule_id"]), {})
            p["rule"] = {"kind": t.get("kind"), "index": t.get("index"), "text": t.get("text")}
        S, F = SessionRecord, Finding
        sessions_of: dict[str, set[str]] = defaultdict(set)  # rule pattern id -> sessions with a finding on it
        parent = {p["pattern_id"]: p["pattern_id"] for p in pats} | {s["pattern_id"]: p["pattern_id"] for p in pats for s in p["sub_patterns"]}
        for pat_id, sid in db.exec(f.sessions(select(F.pattern_id, F.session_id).join(S, S.id == F.session_id)).where(ELIGIBLE, F.procedure_id == procedure_id, F.suppressed.is_(False))).all():
            if pat_id in parent:
                sessions_of[parent[pat_id]].add(sid)
        known = {t.id for t in themes}

        def group(theme_id, name, description, members):
            members = sorted(members, key=lambda p: -p["findings"])
            sessions = set().union(*(sessions_of[p["pattern_id"]] for p in members)) if members else set()
            return {"theme_id": theme_id, "name": name, "description": description, "findings": sum(p["findings"] for p in members), "sessions": len(sessions),
                    "patterns": members, "_sessions": sessions}

        groups = [group(t.id, t.name, t.description, [p for p in pats if p["theme_id"] == t.id]) for t in themes]
        groups = [g for g in groups if g["patterns"]]
        rest = [p for p in pats if p["theme_id"] not in known]
        if rest:
            groups.append(group(None, "Not grouped yet", None, rest))
        return groups

    def rule(self, f: Filters, procedure_id: str, rule_id: str) -> dict[str, Any]:
        """One rule across its wordings (`rule_id` is any of them; see `sopops.lineage`)."""
        f = f.with_(procedure=procedure_id)
        S, R, F = SessionRecord, RuleResult, Finding
        with self._db() as db:
            lid = lineage.lineage_of(db, f.workspace, procedure_id, rule_id)
            seen = lineage.wordings(db, f.workspace, procedure_id).get((procedure_id, lid), [])
            cur = lineage.current(seen) if seen else None
            wordings = [{**w.out(), "text_hash": w.text_hash, "first_seen": w.first_seen, "last_seen": w.last_seen, "results": w.results} for w in seen]
            wordings.sort(key=lambda w: w["last_seen"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            rule_id = lid  # rows and patterns are keyed by the lineage
            rows = [r for r in self._rule_rows(db, f, procedure_id) if r[1] == rule_id]
            kind = cur.kind if cur else rows[0][2] if rows else "step"
            index = cur.index if cur else rows[0][3] if rows else None
            overall = self._rule_stats([(k, s, n) for _p, _r, k, _i, s, n in rows])
            by_rel: dict[Any, list[tuple]] = defaultdict(list)
            for _p, rid, k, _i, s, rel, n in self._rule_rows(db, f, procedure_id, by="release"):
                if rid == rule_id:
                    by_rel[rel].append((k, s, n))
            releases = []
            for rel in sorted(by_rel, key=lambda r: (r is None, r or 0)):
                st = self._rule_stats(by_rel[rel])
                releases.append({"release": rel, **_rate_out(st)})
            by_agent: dict[Any, list[tuple]] = defaultdict(list)
            for _p, rid, k, _i, s, agent, n in self._rule_rows(db, f, procedure_id, by="agent"):
                if rid == rule_id:
                    by_agent[agent].append((k, s, n))
            agents = [{"agent": a, **_rate_out(self._rule_stats(v))} for a, v in sorted(by_agent.items(), key=lambda kv: str(kv[0]))]
            patterns = self._patterns(db, f, procedure_id, rule_id)
            q = f.sessions(select(F, R, S.started_at).join(R, R.id == F.rule_result_id).join(S, S.id == F.session_id))
            q = q.where(ELIGIBLE, F.procedure_id == procedure_id, lineage.in_lineage(F.rule_id, f.workspace, procedure_id, lid), F.suppressed.is_(False))
            recent = [
                {"finding_id": fi.id, "session_id": fi.session_id, "started_at": _utc(at), "status": rr.status, "reason": rr.reason, "evidence": rr.evidence, "pattern_id": fi.pattern_id}
                for fi, rr, at in db.exec(q.order_by(S.started_at.desc()).limit(5)).all()
            ]
        return {"filters": f.as_dict(), "thresholds": THRESHOLDS, "procedure_id": procedure_id, "rule_id": cur.rule_id if cur else lid, "lineage_id": lid,
                "kind": kind, "index": index, "text": cur.text if cur else None, "wordings": wordings,
                "reworded_in": cur.from_release if cur and len(seen) > 1 else None, "overall": _rate_out(overall), "releases": releases,
                "agents": agents, "patterns": patterns, "recent_findings": recent}

    def compare(self, f: Filters, a: int | None = None, b: int | None = None) -> dict[str, Any]:
        """Release B against release A, per rule and per procedure. Default: B = the latest release with sessions in
        the window, A = the one before it."""
        f = f.with_(releases=())
        S, R = SessionRecord, RuleResult
        with self._db() as db:
            seen = sorted(r for r in db.exec(f.sessions(select(S.release_id).where(ELIGIBLE, S.release_id.is_not(None))).distinct()).all())
            if b is None and seen:
                b = seen[-1]
            if a is None and b is not None:
                before = [r for r in seen if r < b]
                a = before[-1] if before else None
            rows_a = self._rule_rows(db, f.with_(releases=(a,)), f.procedure) if a is not None else []
            rows_b = self._rule_rows(db, f.with_(releases=(b,)), f.procedure) if b is not None else []
            # which wording (rule id) of each lineage each release ran; a different one on B: reworded
            lins = lineage.lineages(db, f.workspace, f.procedure)
            ran: dict[tuple[str, str, int], set[str]] = defaultdict(set)
            for pid, rid, rel in db.exec(f.sessions(select(R.procedure_id, R.rule_id, S.release_id).join(S, S.id == R.session_id))
                                         .where(S.release_id.in_([x for x in (a, b) if x is not None])).distinct()).all():
                ran[(pid, lins.get((pid, rid), rid), rel)].add(rid)
            texts = self._rule_texts(db, f.workspace, f.procedure)
            ca = self._checks(db, f.with_(releases=(a,))) if a is not None else []
            cb = self._checks(db, f.with_(releases=(b,))) if b is not None else []
            every = self._checks(db, f)
        def group(rows):
            g: dict[tuple[str, str], list[tuple]] = defaultdict(list)
            for pid, rid, kind, _i, status, n in rows:
                g[(pid, rid)].append((kind, status, n))
            return g
        ga, gb = group(rows_a), group(rows_b)
        out = []
        order = {"step": 0, "never": 1, "warning": 2}
        for key in sorted(set(ga) | set(gb), key=lambda k: (k[0], order.get(texts.get(k, {}).get("kind"), 3), texts.get(k, {}).get("index") or 0)):
            t = texts.get(key, {})
            if t.get("kind") == "warning":
                continue
            sa, sb = self._rule_stats(ga.get(key, [])), self._rule_stats(gb.get(key, []))
            on_a, on_b = ran.get((*key, a), set()) if a is not None else set(), ran.get((*key, b), set()) if b is not None else set()
            change = "new" if key not in ga else "removed" if key not in gb else ("changed" if on_a != on_b else None)
            said = {w["rule_id"]: w["text"] for w in t.get("history", [])}
            rid = min(on_b or on_a or {t.get("rule_id", key[1])})
            ok = sa["n"] >= MIN_N and sb["n"] >= MIN_N
            out.append({
                "procedure_id": key[0], "rule_id": rid, "lineage_id": key[1], "kind": t.get("kind"), "index": t.get("index"), "text": said.get(rid, t.get("text")),
                "a_text": said.get(min(on_a)) if change == "changed" and on_a else None, "rule_change": change,
                "a": _rate_out(sa), "b": _rate_out(sb), "delta_points": delta_points(sb["pass_rate"], sa["pass_rate"]) if ok else None,
                "raw_delta_points": delta_points(sb["pass_rate"], sa["pass_rate"]), "flag": compare_flag(sa["passes"] or 0, sa["n"], sb["passes"] or 0, sb["n"]),
            })
        procs = []
        for pid in sorted({c[1] for c in ca} | {c[1] for c in cb}):
            def rate(cs):
                mine = [c for c in cs if c[1] == pid]
                m = [c for c in mine if c[3] in MEASURABLE]
                fol = sum(c[3] == "followed" for c in m)
                return {"n": len(m), "passes": fol, "pass_rate": pct(fol, len(m)), "interval": wilson(fol, len(m)), "followed": fol,
                        "partial": sum(c[3] == "partial" for c in m), "deviated": sum(c[3] == "deviated" for c in m), "not_measurable": len(mine) - len(m)}
            ra, rb = rate(ca), rate(cb)
            procs.append({"procedure_id": pid, "procedure_name": next((c[2] for c in ca + cb if c[1] == pid and c[2]), pid), "a": ra, "b": rb,
                          "delta_points": delta_points(rb["pass_rate"], ra["pass_rate"]) if ra["n"] >= MIN_N and rb["n"] >= MIN_N else None,
                          "flag": compare_flag(ra["passes"], ra["n"], rb["passes"], rb["n"])})
        # every release in the window: the procedure pass rate on each (by the release each session ran)
        per: dict[int, list] = defaultdict(lambda: [0, 0, set()])
        for sid, _pid, _n, result, _g, rel, _at in every:
            if rel is None:
                continue
            per[rel][2].add(sid)
            if result in MEASURABLE:
                per[rel][1] += 1
                per[rel][0] += result == "followed"
        timeline = [{"release": r, "sessions": len(per[r][2]), "n": per[r][1], "passes": per[r][0], "pass_rate": pct(per[r][0], per[r][1]),
                     "interval": wilson(per[r][0], per[r][1]), "thin": per[r][1] < MIN_N} for r in sorted(per)]
        return {"filters": f.as_dict(), "thresholds": THRESHOLDS, "a": a, "b": b, "releases": seen, "min_n": MIN_N, "flag_points": FLAG_POINTS, "procedures": procs,
                "rules": out, "sessions_a": len({c[0] for c in ca}), "sessions_b": len({c[0] for c in cb}), "timeline": timeline}

    def report(self, f: Filters, examples: int = 3, sessions_per_pattern: int = 12) -> dict[str, Any]:
        """The call-review report: headline numbers, then per procedure its themes, each theme's patterns with the rule
        quoted, example phrasings from the evidence, and the sessions."""
        S, R, F = SessionRecord, RuleResult, Finding
        with self._db() as db:
            tiles = self._tiles(db, f)
            checks = self._checks(db, f)
            per_session = f.sessions(select(F.session_id).join(S, S.id == F.session_id)).where(ELIGIBLE, F.suppressed.is_(False))
            if f.procedure:
                per_session = per_session.where(F.procedure_id == f.procedure)
            multi = db.exec(select(func.count()).select_from(per_session.group_by(F.session_id).having(func.count(func.distinct(F.rule_id)) > 1).subquery())).one()
            worst: dict[str, str] = {}
            for sid, _pid, _name, result, *_ in checks:
                if sid not in worst or SEVERITY[result] > SEVERITY[worst[sid]]:
                    worst[sid] = result
            need = {sid for sid, r in worst.items() if r == "deviated"}
            procs = []
            for pid in sorted({c[1] for c in checks}) if not f.procedure else [f.procedure]:
                themes = self._themes(db, f, pid)
                name = next((c[2] for c in checks if c[1] == pid and c[2]), pid)
                for th in themes:
                    for p in th["patterns"]:
                        leaves = [s for s in p["sub_patterns"] if s["findings"]] or [p]
                        ids = [x["pattern_id"] for x in leaves] + [p["pattern_id"]]
                        rows = db.exec(
                            f.sessions(select(F.pattern_id, F.session_id, R.evidence, R.status, S.started_at).join(R, R.id == F.rule_result_id).join(S, S.id == F.session_id))
                            .where(ELIGIBLE, F.pattern_id.in_(ids), F.suppressed.is_(False)).order_by(S.started_at.desc())
                        ).all()
                        by_pat: dict[str, list] = defaultdict(list)
                        for pat_id, sid, ev, status, at in rows:
                            by_pat[pat_id].append((sid, ev, status, _utc(at)))
                        for x in [p, *p["sub_patterns"]]:
                            got = by_pat.get(x["pattern_id"], [])
                            if x is p:
                                got = [g for leaf in leaves for g in by_pat.get(leaf["pattern_id"], [])] if p["sub_patterns"] else got
                            x["examples"] = _examples(got, examples)
                            every = list(dict.fromkeys(g[0] for g in got))
                            x["session_ids"] = every[:sessions_per_pattern]
                            x["need_attention_pct"] = pct(len(set(every) & need), len(need)) if need else None
                    th["need_attention_pct"] = pct(len(th.pop("_sessions") & need), len(need)) if need else None
                procs.append({"procedure_id": pid, "procedure_name": name, "themes": themes})
        return {"filters": f.as_dict(), "headline": {**tiles, "multi_problem_sessions": multi}, "procedures": procs}

    def patterns(self, f: Filters, procedure_id: str | None = None, rule_id: str | None = None) -> dict[str, Any]:
        with self._db() as db:
            pats = self._patterns(db, f, procedure_id or f.procedure, rule_id)
            texts = self._rule_texts(db, f.workspace, procedure_id or f.procedure)
        for p in pats:
            t = texts.get((p["procedure_id"], p["rule_id"]), {})
            p["rule"] = {"kind": t.get("kind"), "index": t.get("index"), "text": t.get("text")}
        pats.sort(key=lambda p: (p["procedure_id"], -p["findings"], p["name"]))
        return {"items": pats}

    def pattern(self, f: Filters, pattern_id: str, examples: int = 5) -> dict[str, Any] | None:
        """Counts leave suppressed findings out; the examples and sessions keep them, so a pattern a reviewer called
        acceptable still shows what it is."""
        S, R, F = SessionRecord, RuleResult, Finding
        with self._db() as db:
            p = db.get(Pattern, pattern_id)
            if p is None or p.workspace_id != f.workspace:
                return None
            rule_pid = p.parent_id or p.id
            family = self._patterns(db, f.with_(procedure=None), p.procedure_id, p.rule_id)
            rule = next((x for x in family if x["pattern_id"] == rule_pid), None)
            me = rule if p.kind == "rule" else next((x for x in (rule or {}).get("sub_patterns", []) if x["pattern_id"] == p.id), None)
            if me is None:
                me = _pattern_out(p, 0, 0)
            ids = [p.id] + ([x["pattern_id"] for x in rule["sub_patterns"]] if p.kind == "rule" and rule else [])
            rows = db.exec(
                f.sessions(select(F.pattern_id, F.session_id, R.evidence, R.status, S.started_at).join(R, R.id == F.rule_result_id).join(S, S.id == F.session_id))
                .where(ELIGIBLE, F.pattern_id.in_(ids)).order_by(S.started_at.desc())
            ).all()
            got = [(sid, ev, st, _utc(at)) for _pid, sid, ev, st, at in rows]
            t = self._rule_texts(db, f.workspace, p.procedure_id).get((p.procedure_id, p.rule_id), {})
        return {**me, "rule": {"kind": t.get("kind"), "index": t.get("index"), "text": t.get("text")}, "sub_patterns": (rule or {}).get("sub_patterns", []) if p.kind == "rule" else [],
                "examples": _examples(got, examples), "session_ids": list(dict.fromkeys(g[0] for g in got))}

    def releases(self, workspace: str) -> list[int]:
        with self._db() as db:
            return sorted(r for r in db.exec(select(SessionRecord.release_id).where(SessionRecord.workspace_id == workspace, SessionRecord.release_id.is_not(None)).distinct()).all())


def _rate_out(st: dict[str, Any]) -> dict[str, Any]:
    return {"n": st["n"], "passes": st["passes"], "fails": st["fails"], "pass_rate": st["pass_rate"], "interval": wilson(st["passes"] or 0, st["n"]) if st["n"] else None,
            "not_counted": st["not_counted"], "thin": st["n"] < MIN_N, "counts": st["counts"]}


def _pattern_out(p: Pattern, findings: int, sessions: int) -> dict[str, Any]:
    return {"pattern_id": p.id, "procedure_id": p.procedure_id, "rule_id": p.rule_id, "parent_id": p.parent_id, "kind": p.kind, "name": p.name,
            "description": p.description, "theme_id": p.theme_id, "status": p.status, "findings": findings, "sessions": sessions,
            "total_findings": p.finding_count, "first_seen": _utc(p.first_seen), "last_seen": _utc(p.last_seen), "clustered_at": _utc(p.clustered_at)}


def _examples(rows: list, k: int) -> list[dict[str, Any]]:
    """Up to k distinct quotes of what was said, newest first, one per session."""
    out, seen_q, seen_s = [], set(), set()
    for sid, evidence, status, at in rows:
        if sid in seen_s:
            continue
        for e in evidence or []:
            q = (e.get("quote") or "").strip()
            if q and e.get("source", "transcript") == "transcript" and q.lower() not in seen_q:
                out.append({"session_id": sid, "turn": e.get("turn"), "quote": q, "status": status})
                seen_q.add(q.lower())
                seen_s.add(sid)
                break
        if len(out) >= k:
            break
    return out
