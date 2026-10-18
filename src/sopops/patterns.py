"""Patterns and themes: findings grouped so a person sees what keeps going wrong, not one session at a time.

- Every rule with findings has one **rule pattern** (`kind = rule`). Its name is made from the rule, no model needed.
- Once a rule has `SPLIT_AT` findings, the reasoning model splits its findings into at most 5 named failure modes:
  **sub-patterns** (`kind = sub`), plus one `other` sub-pattern for findings that fit none.
- New findings on a split rule are **assigned** to one of its sub-patterns, or to `other`, by a decision (`sopops.ai.decide`).
- A rule is **reclustered** when `RECLUSTER_NEW` findings arrived since its last clustering, or when `other` holds
  more than `OTHER_SHARE` of its findings (and at least `OTHER_MIN_NEW` arrived since), plus by the daily cron
  (`recluster_due`) and on demand (`sopops recluster`, `POST .../patterns/recluster`).
- Reclustering keeps ids and names stable: each new cluster is matched to the old sub-pattern it shares the most
  findings with (Jaccard over finding ids, at least `MATCH_JACCARD`), and keeps that pattern's id and name, so
  verdicts on it keep applying. Old sub-patterns that match nothing are retired, not deleted.
- **Themes** group a procedure's rule patterns by the phase of the session where they happen; a model names and
  orders them, reusing existing theme names where they still fit (by key, else by member overlap).

A finding's `pattern_id` is its most specific pattern: its sub-pattern once the rule is split, else the rule pattern.
Each function takes an `AI` (tests pass a fake) and is plain, so it runs inline and as a Hatchet task.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlmodel import Session, func, select

from .ai import decide
from .ai import lm as ai_lm
from .ai.signatures import (
    VERSIONS,
    ClusterFindings,
    FailureMode,
    FindingIn,
    GroupThemes,
    PatternIn,
    ThemeGroup,
    ThemeIn,
)
from .orm import Finding, Pattern, ProcedureCheck, RuleResult, SessionRecord, Theme

if TYPE_CHECKING:
    from .ai.lm import AI
    from .store import Store

log = logging.getLogger("sopops.patterns")

SPLIT_AT = 8  # findings on a rule before it's split into failure modes
MAX_MODES = 5
RECLUSTER_NEW = 25  # findings since the last clustering
OTHER_SHARE = 0.25
OTHER_MIN_NEW = 5  # the "other" trigger needs at least this many new findings since the last clustering
MATCH_JACCARD = 0.3
CLUSTER_SAMPLE = 150  # most recent findings shown to the model; older ones are assigned like new ones
ASSIGN_BATCH = 25
MAX_THEMES = 6

KIND_LABEL = {"step": "Step", "never": "Never", "warning": "Warning sign"}


def rule_pattern_name(kind: str, index: int) -> str:
    return {"step": f"Step {index} missed", "never": f"Never rule {index} broken", "warning": f"Warning sign {index} not acted on"}.get(kind, f"Rule {index}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> str:
    return str(uuid.uuid4())


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


# --- the facts a model sees about a finding ------------------------------------------------------------------


@dataclass
class FindingView:
    id: str
    session_id: str
    procedure_id: str
    rule_id: str
    kind: str
    index: int
    rule_text: str
    status: str
    reason: str
    quotes: list[str]
    started_at: datetime | None = None



def _views(db: Session, findings: list[Finding]) -> list[FindingView]:
    if not findings:
        return []
    rr = {r.id: r for r in db.exec(select(RuleResult).where(RuleResult.id.in_([f.rule_result_id for f in findings])))}
    out = []
    for f in findings:
        r = rr.get(f.rule_result_id)
        if r is None:
            continue
        quotes = [str(e.get("quote")) for e in (r.evidence or []) if e.get("quote") and e.get("source", "transcript") == "transcript"]
        out.append(FindingView(f.id, f.session_id, f.procedure_id, f.rule_id, r.kind, r.index, r.rule_text, r.status, (r.reason or "").split(" (Lenient:")[0], quotes))
    return out


def clusters_of(modes: list[FailureMode], keys: dict[str, FindingView]) -> list[tuple[str, str, set[str]]]:
    """The model's failure modes as (name, description, finding ids): known keys only, each finding in at most one
    mode, modes with at least 2 findings, at most MAX_MODES."""
    seen: set[str] = set()
    out = []
    for m in modes:
        ids = {keys[k].id for k in dict.fromkeys(m.findings) if k in keys and k not in seen}
        seen |= {k for k in m.findings if k in keys}
        if len(ids) >= 2 and len(out) < MAX_MODES:
            out.append((m.name.strip()[:200], m.description.strip(), ids))
    return out


def themes_of(themes: list[ThemeGroup], pkeys: dict[str, Pattern], tkeys: dict[str, Theme]) -> list[tuple[str, str, str, list[str]]]:
    """The model's themes as (key, name, description, pattern ids): every pattern in exactly one theme (any it left
    out go in the last), existing keys used once, at most MAX_THEMES (extra themes merge into the last)."""
    placed: set[str] = set()
    used: set[str] = set()
    out: list[tuple[str, str, str, list[str]]] = []
    for t in themes:
        pats = [k for k in dict.fromkeys(t.patterns) if k in pkeys and k not in placed]
        placed |= set(pats)
        key = t.key if t.key in tkeys and t.key not in used else "new"
        used.add(key)
        if len(out) < MAX_THEMES:
            out.append((key, t.name.strip()[:200], t.description.strip(), [pkeys[k].id for k in pats]))
        elif out:
            out[-1][3].extend(pkeys[k].id for k in pats)
    rest = [pkeys[k].id for k in pkeys if k not in placed]
    if rest:
        if out:
            out[-1][3].extend(rest)
        else:
            out.append(("new", "Other", "Patterns not grouped yet.", rest))
    return [g for g in out if g[3]]


def _rule_line(kind: str, index: int, text: str) -> str:
    return f"{KIND_LABEL.get(kind, kind)} {index}: {text}"


# --- the store side ------------------------------------------------------------------------------------------------


@dataclass
class Outcome:
    """What a run changed, for logs, tests and the CLI."""

    assigned: int = 0
    reclustered: list[str] = field(default_factory=list)  # rule ids
    themed: list[str] = field(default_factory=list)  # procedure ids
    due: list[tuple[str, str]] = field(default_factory=list)  # (procedure_id, rule_id) left for a background run


class Patterns:
    def __init__(self, store: Store):
        self.store = store

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    # --- assign ----------------------------------------------------------------------------------------------

    def assign_session(self, workspace: str, session_id: str, ai: AI | None = None, recluster: bool = True) -> Outcome:
        """Put a checked session's unassigned findings into patterns, then recluster and regroup what's due
        (`recluster=False`: only report what's due, for a background job to pick up)."""
        with self._db() as db:
            findings = list(db.exec(select(Finding).where(Finding.workspace_id == workspace, Finding.session_id == session_id, Finding.pattern_id.is_(None))))
        return self._assign_and_follow_up(workspace, findings, ai, recluster)

    def assign_pending(self, workspace: str | None = None, ai: AI | None = None, recluster: bool = True) -> Outcome:
        """Every finding without a pattern (a backfill, or after a model outage)."""
        out = Outcome()
        with self._db() as db:
            q = select(Finding).where(Finding.pattern_id.is_(None))
            if workspace:
                q = q.where(Finding.workspace_id == workspace)
            findings = list(db.exec(q))
        by_ws: dict[str, list[Finding]] = defaultdict(list)
        for f in findings:
            by_ws[f.workspace_id].append(f)
        for ws, fs in by_ws.items():
            o = self._assign_and_follow_up(ws, fs, ai, recluster)
            out.assigned += o.assigned
            out.reclustered += o.reclustered
            out.themed += o.themed
            out.due += o.due
        return out

    def _assign_and_follow_up(self, ws: str, findings: list[Finding], ai: AI | None, recluster: bool) -> Outcome:
        out = Outcome()
        if not findings:
            return out
        ai = ai or ai_lm.get_ai()
        out.assigned = self._assign(ws, findings, ai)
        rules = sorted({(f.procedure_id, f.rule_id) for f in findings})
        for pid, rid in rules:
            if self.due(ws, pid, rid):
                if recluster:
                    if self.recluster(ws, pid, rid, ai):
                        out.reclustered.append(rid)
                else:
                    out.due.append((pid, rid))
        if recluster:
            for pid in sorted({p for p, _ in rules}):
                if self.themes_due(ws, pid) and self.regroup_themes(ws, pid, ai):
                    out.themed.append(pid)
        return out

    def _assign(self, ws: str, findings: list[Finding], ai: AI) -> int:
        """Rule pattern for every finding (created if new); the fast model picks a sub-pattern on split rules."""
        now = _now()
        with self._db() as db, db.begin():
            ids = [f.id for f in findings]
            findings = list(db.exec(select(Finding).where(Finding.id.in_(ids))))
            views = {v.id: v for v in _views(db, findings)}
            rule_pats: dict[tuple[str, str], Pattern] = {}
            for f in findings:
                key = (f.procedure_id, f.rule_id)
                if key not in rule_pats:
                    rule_pats[key] = self._rule_pattern(db, ws, f.procedure_id, f.rule_id, views.get(f.id), now)
            subs = self._subs_by_rule(db, ws, list(rule_pats.values()))
            simple = [f for f in findings if not subs.get(rule_pats[(f.procedure_id, f.rule_id)].id)]
            for f in simple:
                f.pattern_id = rule_pats[(f.procedure_id, f.rule_id)].id
                db.add(f)
            split = [f for f in findings if subs.get(rule_pats[(f.procedure_id, f.rule_id)].id) and f.id in views]
            for i in range(0, len(split), ASSIGN_BATCH):
                batch = split[i : i + ASSIGN_BATCH]
                choice = self._choose(ai, [views[f.id] for f in batch], {f.id: subs[rule_pats[(f.procedure_id, f.rule_id)].id] for f in batch})
                for f in batch:
                    f.pattern_id = choice[f.id]
                    db.add(f)
            for f in findings:  # no rule result to describe it (shouldn't happen): the rule pattern
                if f.pattern_id is None:
                    f.pattern_id = rule_pats[(f.procedure_id, f.rule_id)].id
                    db.add(f)
            db.flush()
            for p in rule_pats.values():
                self._refresh_counts(db, p)
        return len(findings)

    def _choose(self, ai: AI, views: list[FindingView], options: dict[str, list[Pattern]]) -> dict[str, str]:
        """{finding id: pattern id}: a decision picks among each finding's rule's sub-patterns (other included)."""
        keys = {v.id: f"f{i + 1}" for i, v in enumerate(views)}
        mode_key: dict[str, str] = {}
        rules: dict[str, list[FindingView]] = defaultdict(list)
        for v in views:
            rules[v.rule_id].append(v)
        groups = []
        for vs in rules.values():
            modes = {}
            for pat in options[vs[0].id]:
                if pat.kind != "other":
                    mode_key[pat.id] = f"m{len(mode_key) + 1}"
                    modes[mode_key[pat.id]] = f"{pat.name}. {pat.description or ''}".strip()
            groups.append(decide.AssignGroup(
                rule=_rule_line(vs[0].kind, vs[0].index, vs[0].rule_text), modes=modes,
                findings={keys[v.id]: {"status": v.status, "reason": v.reason, "quotes": v.quotes[:3]} for v in vs},
            ))
        try:
            picked = decide.assign(ai, groups)
        except Exception:
            log.exception("assigning findings to patterns failed; they go to other")
            picked = {}
        return self._picked(views, options, keys, mode_key, picked)

    def _picked(self, views: list[FindingView], options: dict[str, list[Pattern]], keys: dict[str, str], mode_key: dict[str, str], picked: dict[str, str]) -> dict[str, str]:
        out = {}
        for v in views:
            opts = options[v.id]
            by_key = {mode_key[p.id]: p.id for p in opts if p.id in mode_key}
            other = next(p.id for p in opts if p.kind == "other")
            out[v.id] = by_key.get(picked.get(keys[v.id], "other"), other)  # a mode of another rule counts as other
        return out

    def _rule_pattern(self, db: Session, ws: str, procedure_id: str, rule_id: str, view: FindingView | None, now: datetime) -> Pattern:
        p = db.exec(select(Pattern).where(Pattern.workspace_id == ws, Pattern.procedure_id == procedure_id, Pattern.rule_id == rule_id, Pattern.kind == "rule")).first()
        if p is None:
            kind, index, text = (view.kind, view.index, view.rule_text) if view else ("step", 0, "")
            p = Pattern(id=_id(), workspace_id=ws, procedure_id=procedure_id, rule_id=rule_id, kind="rule", name=rule_pattern_name(kind, index),
                        description=text, created_at=now, updated_at=now)
            db.add(p)
            db.flush()
        return p

    def _subs_by_rule(self, db: Session, ws: str, rule_pats: list[Pattern]) -> dict[str, list[Pattern]]:
        """Active sub-patterns (other last) per rule pattern id; only rules that have been split."""
        if not rule_pats:
            return {}
        rows = db.exec(select(Pattern).where(Pattern.parent_id.in_([p.id for p in rule_pats]), Pattern.status == "active"))
        out: dict[str, list[Pattern]] = defaultdict(list)
        for p in rows:
            out[p.parent_id].append(p)  # type: ignore[index]
        return {k: sorted(v, key=lambda p: (p.kind == "other", p.created_at, p.name)) for k, v in out.items() if any(p.kind == "sub" for p in v)}

    def _refresh_counts(self, db: Session, rule_pat: Pattern) -> None:
        """Counts and first/last seen for a rule pattern and its sub-patterns, from their findings."""
        family = [rule_pat, *db.exec(select(Pattern).where(Pattern.parent_id == rule_pat.id))]
        ids = [p.id for p in family]
        rows = db.exec(
            select(Finding.pattern_id, func.count(Finding.id), func.count(func.distinct(Finding.session_id)), func.min(SessionRecord.started_at), func.max(SessionRecord.started_at))
            .join(SessionRecord, SessionRecord.id == Finding.session_id)
            .where(Finding.pattern_id.in_(ids))
            .group_by(Finding.pattern_id)
        ).all()
        stats = {r[0]: r[1:] for r in rows}
        total = db.exec(
            select(func.count(Finding.id), func.count(func.distinct(Finding.session_id)), func.min(SessionRecord.started_at), func.max(SessionRecord.started_at))
            .join(SessionRecord, SessionRecord.id == Finding.session_id)
            .where(Finding.workspace_id == rule_pat.workspace_id, Finding.procedure_id == rule_pat.procedure_id, Finding.rule_id == rule_pat.rule_id)
        ).one()
        now = _now()
        for p in family:
            n, s, first, last = total if p.kind == "rule" else stats.get(p.id, (0, 0, None, None))
            p.finding_count, p.session_count, p.first_seen, p.last_seen, p.updated_at = n or 0, s or 0, first, last, now
            db.add(p)

    # --- recluster -----------------------------------------------------------------------------------------------

    def due(self, ws: str, procedure_id: str, rule_id: str) -> bool:
        with self._db() as db:
            rp = db.exec(select(Pattern).where(Pattern.workspace_id == ws, Pattern.procedure_id == procedure_id, Pattern.rule_id == rule_id, Pattern.kind == "rule")).first()
            if rp is None:
                return False
            subs = list(db.exec(select(Pattern).where(Pattern.parent_id == rp.id, Pattern.status == "active")))
            return reason_due(rp, subs) is not None

    def recluster_due(self, workspace: str | None = None, ai: AI | None = None) -> Outcome:
        """The daily pass: every rule whose triggers fire, or that gained findings since it was last clustered and has
        enough to split; then themes for procedures that changed."""
        ai = ai or ai_lm.get_ai()
        out = Outcome()
        with self._db() as db:
            q = select(Pattern).where(Pattern.kind == "rule")
            if workspace:
                q = q.where(Pattern.workspace_id == workspace)
            rule_pats = list(db.exec(q))
        touched: set[tuple[str, str]] = set()
        for rp in rule_pats:
            daily = rp.finding_count >= SPLIT_AT and rp.finding_count != rp.clustered_count
            if (daily or self.due(rp.workspace_id, rp.procedure_id, rp.rule_id)) and self.recluster(rp.workspace_id, rp.procedure_id, rp.rule_id, ai, force=True):
                out.reclustered.append(rp.rule_id)
                touched.add((rp.workspace_id, rp.procedure_id))
        for ws, pid in sorted({(p.workspace_id, p.procedure_id) for p in rule_pats}):
            if ((ws, pid) in touched or self.themes_due(ws, pid)) and self.regroup_themes(ws, pid, ai, force=(ws, pid) in touched):
                out.themed.append(pid)
        return out

    def recluster_all(self, workspace: str, procedure_id: str | None = None, ai: AI | None = None) -> Outcome:
        """Recluster every rule with enough findings (on demand), then regroup the themes of what changed."""
        ai = ai or ai_lm.get_ai()
        out = Outcome()
        with self._db() as db:
            q = select(Pattern).where(Pattern.workspace_id == workspace, Pattern.kind == "rule")
            if procedure_id:
                q = q.where(Pattern.procedure_id == procedure_id)
            rule_pats = list(db.exec(q))
        for rp in rule_pats:
            if self.recluster(workspace, rp.procedure_id, rp.rule_id, ai, force=True):
                out.reclustered.append(rp.rule_id)
        for pid in sorted({p.procedure_id for p in rule_pats}):
            if self.regroup_themes(workspace, pid, ai, force=True):
                out.themed.append(pid)
        return out

    def recluster(self, ws: str, procedure_id: str, rule_id: str, ai: AI | None = None, force: bool = False) -> bool:
        """Split a rule's findings into failure modes and match them to the existing sub-patterns. Returns whether it
        ran (not when nothing is due, unless `force`; never with fewer than SPLIT_AT findings)."""
        ai = ai or ai_lm.get_ai()
        with self._db() as db:
            rp = db.exec(select(Pattern).where(Pattern.workspace_id == ws, Pattern.procedure_id == procedure_id, Pattern.rule_id == rule_id, Pattern.kind == "rule")).first()
            if rp is None:
                return False
            subs = list(db.exec(select(Pattern).where(Pattern.parent_id == rp.id, Pattern.status == "active")))
            if rp.finding_count < SPLIT_AT or (not force and reason_due(rp, subs) is None):
                return False
            findings = list(
                db.exec(
                    select(Finding).join(SessionRecord, SessionRecord.id == Finding.session_id)
                    .where(Finding.workspace_id == ws, Finding.procedure_id == procedure_id, Finding.rule_id == rule_id)
                    .order_by(SessionRecord.started_at.desc(), Finding.id)
                )
            )
            sample = findings[:CLUSTER_SAMPLE]
            views = _views(db, sample)
        if len(views) < SPLIT_AT:
            return False
        keys = {f"f{i + 1}": v for i, v in enumerate(views)}
        v0 = views[0]
        reply = ai.predict(
            "reasoning", ClusterFindings, rule=_rule_line(v0.kind, v0.index, v0.rule_text),
            findings=[FindingIn(key=k, status=v.status, reason=v.reason, quotes=v.quotes[:3]) for k, v in keys.items()],
        )
        self._apply_clusters(ws, rp.id, clusters_of(reply.modes, keys), [v.id for v in views], ai.model("reasoning"), VERSIONS["cluster"])
        rest = [f.id for f in findings[CLUSTER_SAMPLE:]]
        if rest:  # findings older than the model saw keep their sub-pattern if it's still active, else get assigned
            with self._db() as db, db.begin():
                active = set(db.exec(select(Pattern.id).where(Pattern.parent_id == rp.id, Pattern.status == "active", Pattern.kind == "sub")))
                moved = [f for f in db.exec(select(Finding).where(Finding.id.in_(rest))) if f.pattern_id not in active]
                for f in moved:
                    f.pattern_id = None
                    db.add(f)
            if moved:
                self._assign(ws, moved, ai)
        return True

    def _apply_clusters(self, ws: str, rule_pattern_id: str, clusters: list[tuple[str, str, set[str]]], seen: list[str], model: str | None, version: str) -> None:
        now = _now()
        with self._db() as db, db.begin():
            rp = db.get(Pattern, rule_pattern_id)
            assert rp is not None
            old = list(db.exec(select(Pattern).where(Pattern.parent_id == rp.id)))
            old_subs = [p for p in old if p.kind == "sub"]
            members: dict[str, set[str]] = defaultdict(set)
            for fid, pid in db.exec(select(Finding.id, Finding.pattern_id).where(Finding.pattern_id.in_([p.id for p in old_subs]))):
                members[pid].add(fid)
            # greedy one-to-one matching, best overlap first
            pairs = sorted(
                ((jaccard(c[2], members[p.id]), i, p.id) for i, c in enumerate(clusters) for p in old_subs),
                key=lambda x: (-x[0], x[1], x[2]),
            )
            match: dict[int, Pattern] = {}
            used: set[str] = set()
            by_id = {p.id: p for p in old_subs}
            for score, i, pid in pairs:
                if score < MATCH_JACCARD or i in match or pid in used:
                    continue
                match[i], _ = by_id[pid], used.add(pid)
            target: dict[str, str] = {}
            for i, (name, desc, fids) in enumerate(clusters):
                p = match.get(i)
                if p is None:
                    p = Pattern(id=_id(), workspace_id=ws, procedure_id=rp.procedure_id, rule_id=rp.rule_id, parent_id=rp.id, kind="sub", name=name,
                                description=desc, theme_id=rp.theme_id, created_at=now, updated_at=now)
                p.status, p.model, p.prompt_version, p.updated_at = "active", model, version, now  # a matched pattern keeps its id and name
                db.add(p)
                for fid in fids:
                    target[fid] = p.id
            for p in old_subs:
                if p.id not in used:
                    p.status, p.updated_at = "retired", now
                    db.add(p)
            other = next((p for p in old if p.kind == "other"), None)
            if other is None:
                other = Pattern(id=_id(), workspace_id=ws, procedure_id=rp.procedure_id, rule_id=rp.rule_id, parent_id=rp.id, kind="other",
                                name="Other", description="Findings on this rule that fit none of its failure modes.", theme_id=rp.theme_id, created_at=now, updated_at=now)
            other.status = "active"
            db.add(other)
            db.flush()
            for f in db.exec(select(Finding).where(Finding.id.in_(seen))):
                f.pattern_id = target.get(f.id, other.id)
                db.add(f)
            db.flush()
            self._refresh_counts(db, rp)
            rp.clustered_count, rp.clustered_at, rp.model, rp.prompt_version = rp.finding_count, now, model, version
            db.add(rp)

    # --- themes ----------------------------------------------------------------------------------------------------

    def themes_due(self, ws: str, procedure_id: str) -> bool:
        """Some rule pattern of the procedure has no theme yet."""
        with self._db() as db:
            return db.exec(
                select(func.count(Pattern.id)).where(Pattern.workspace_id == ws, Pattern.procedure_id == procedure_id, Pattern.kind == "rule", Pattern.theme_id.is_(None))
            ).one() > 0

    def regroup_themes(self, ws: str, procedure_id: str, ai: AI | None = None, force: bool = False) -> bool:
        ai = ai or ai_lm.get_ai()
        if not force and not self.themes_due(ws, procedure_id):
            return False
        with self._db() as db:
            rule_pats = list(db.exec(select(Pattern).where(Pattern.workspace_id == ws, Pattern.procedure_id == procedure_id, Pattern.kind == "rule")))
            if not rule_pats:
                return False
            subs: dict[str, list[Pattern]] = defaultdict(list)
            for p in db.exec(select(Pattern).where(Pattern.parent_id.in_([p.id for p in rule_pats]), Pattern.kind == "sub", Pattern.status == "active")):
                subs[p.parent_id].append(p)  # type: ignore[index]
            themes = list(db.exec(select(Theme).where(Theme.workspace_id == ws, Theme.procedure_id == procedure_id).order_by(Theme.position)))
            name = db.exec(select(ProcedureCheck.procedure_name).where(ProcedureCheck.workspace_id == ws, ProcedureCheck.procedure_id == procedure_id).limit(1)).first()
            kinds = {
                rid: (kind, index)
                for rid, kind, index in db.exec(
                    select(RuleResult.rule_id, RuleResult.kind, RuleResult.index)
                    .where(RuleResult.workspace_id == ws, RuleResult.procedure_id == procedure_id, RuleResult.rule_id.in_([p.rule_id for p in rule_pats]))
                    .distinct()
                )
            }
        order = {"step": 0, "never": 1, "warning": 2}
        rule_pats.sort(key=lambda p: (order.get(kinds.get(p.rule_id, ("", 0))[0], 3), kinds.get(p.rule_id, ("", 0))[1]))
        pkeys = {f"r{i + 1}": p for i, p in enumerate(rule_pats)}
        tkeys = {f"t{i + 1}": t for i, t in enumerate(themes)}
        patterns = [
            PatternIn(key=k, rule=_rule_line(*kinds.get(p.rule_id, ("", 0)), p.description or ""), findings=p.finding_count, failure_modes=[x.name for x in subs.get(p.id, [])])
            for k, p in pkeys.items()
        ]
        existing = [ThemeIn(key=k, name=t.name, description=t.description or "", patterns=[pk for pk, p in pkeys.items() if p.theme_id == t.id]) for k, t in tkeys.items()]
        reply = ai.predict("reasoning", GroupThemes, procedure=name or procedure_id, patterns=patterns, existing_themes=existing)
        self._apply_themes(ws, procedure_id, themes_of(reply.themes, pkeys, tkeys), tkeys)
        return True

    def _apply_themes(self, ws: str, procedure_id: str, groups: list[tuple[str, str, str, list[str]]], tkeys: dict[str, Theme]) -> None:
        now = _now()
        with self._db() as db, db.begin():
            old = {t.id: t for t in db.exec(select(Theme).where(Theme.workspace_id == ws, Theme.procedure_id == procedure_id))}
            old_members: dict[str, set[str]] = defaultdict(set)
            for p in db.exec(select(Pattern).where(Pattern.workspace_id == ws, Pattern.procedure_id == procedure_id, Pattern.kind == "rule")):
                if p.theme_id:
                    old_members[p.theme_id].add(p.id)
            used: set[str] = set()
            assigned: dict[str, str] = {}
            for position, (key, name, desc, members) in enumerate(groups):
                theme = old.get(tkeys[key].id) if key in tkeys else None
                if theme is not None and old_members.get(theme.id) and not set(members) & old_members[theme.id]:
                    theme = None  # an existing key given to patterns that share nothing with it: not the same theme
                if theme is None:  # "new": still reuse an unused old theme with most of the same patterns
                    best = max(((jaccard(set(members), m), tid) for tid, m in old_members.items() if tid not in used), default=(0.0, None))
                    if best[0] >= 0.5:
                        theme = old[best[1]]
                if theme is None or theme.id in used:
                    theme = Theme(id=_id(), workspace_id=ws, procedure_id=procedure_id, name=name.strip()[:200], description=desc.strip(), created_at=now, updated_at=now)
                theme.position, theme.updated_at = position, now  # an existing theme keeps its name
                if not theme.description:
                    theme.description = desc.strip()
                db.add(theme)
                used.add(theme.id)
                for pid in members:
                    assigned[pid] = theme.id
            db.flush()
            for p in db.exec(select(Pattern).where(Pattern.workspace_id == ws, Pattern.procedure_id == procedure_id)):
                tid = assigned.get(p.id if p.kind == "rule" else (p.parent_id or ""))
                if tid and p.theme_id != tid:
                    p.theme_id = tid
                    db.add(p)
            for tid, t in old.items():
                if tid not in used:
                    db.delete(t)


def reason_due(rule_pat: Pattern, subs: list[Pattern]) -> str | None:
    """Why a rule should be (re)clustered now, or None."""
    n = rule_pat.finding_count
    split = any(p.kind == "sub" for p in subs)
    if not split:  # never split, or the last clustering found no failure mode with two findings
        if rule_pat.clustered_count == 0:
            return "split" if n >= SPLIT_AT else None
        return "split" if n - rule_pat.clustered_count >= SPLIT_AT else None
    new = n - rule_pat.clustered_count
    if new >= RECLUSTER_NEW:
        return "new_findings"
    other = next((p.finding_count for p in subs if p.kind == "other"), 0)
    if n and other / n > OTHER_SHARE and new >= OTHER_MIN_NEW:
        return "other_share"
    return None
