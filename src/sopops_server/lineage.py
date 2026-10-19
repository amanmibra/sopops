"""Rule lineage: which rule a reworded rule replaced, so its history, patterns, verdicts and review state follow it.

sopc derives a rule's id from its text, so rewording a rule (by hand, or by applying a suggestion) gives it a new id.
That's by design: a session records exactly the wording it ran, and keeps it. sopops records the chain instead, in
`rule_lineage`: each reworded rule points to the rule it replaced, and every row carries the chain's `lineage_id` (the
first rule's id). A rule with no row is its own lineage.

**When it's written.** At publish (`record`), each procedure's rules in the previous live release are compared with the
new release's. Rules whose id is in both continue as they are. The rest are matched old to new, one to one, only within
the same procedure and the same kind (step, never rule, warning sign), in this order:

1. `suggestion`: an applied suggestion whose edited file is the one published knows which rule it edited, and the
   rule now at that rule's position (or, if the edit moved it, the most similar new rule) is its new wording.
2. `matched`, same position: the new rule at the same index, if its similarity is at least `SAME_POSITION_MIN`.
3. `matched`, anywhere: the most similar remaining pair, best first, if at least `ANYWHERE_MIN` (a rule that was moved
   and reworded).

A rule left unmatched is new; an old one left unmatched was removed. Similarity is `similarity`: token Jaccard over
lowercase words, without a few function words, deterministic and cheap. On the fixture and walkthrough procedures,
rewordings score 0.38 to 1.0 and two different rules of one procedure at most 0.29 (`tests/test_lineage.py`). A rule
whose text comes back (a revert) rejoins its own lineage; a link never makes a cycle.

**Fixing it by hand.** `link` (a person says a rule replaced another) and `unlink` (it's a new rule) write a `manual`
row, which the matcher and `rebuild` never overwrite. `rebuild` recomputes the matched and suggestion rows of every
release, each against the release before it; the migration that adds the table runs it on existing data.

After any change, `follow` (`sopops_server.patterns.rekey`) moves rule patterns onto their lineage. Reading helpers
(`lineages`, `lineage_of`, `members`, `in_lineage`, `expand`, `latest`, `wordings`) are what patterns, verdicts, suggestions,
Insights, sessions and calibration use to follow a rule across its wordings.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, delete, func, or_, select

from . import merge
from .errors import Conflict, NotFound
from .orm import FileVersion, Release, RuleLineage, RuleResult, SessionRecord, Suggestion, Workspace
from .sopc import Invalid, Sopc

if TYPE_CHECKING:
    from .store import Store

log = logging.getLogger("sopops.lineage")

SAME_POSITION_MIN = 0.35  # a changed rule and the new rule at its position: the same rule, reworded
ANYWHERE_MIN = 0.5  # a changed rule and a new rule elsewhere in the procedure: moved and reworded
FIX_STATUSES = ("applied", "pr_open", "merged")  # suggestions whose edit can reach a release
_STOP = frozenset("a an the to of and or is are be it its in on at for with that this you your if any by as do does".split())

# (db, workspace, procedure id): called once per procedure whose lineage changed, inside the same transaction
Follow = Callable[[Session, str, str], None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- the matcher -----------------------------------------------------------------------------------------------------


def tokens(text: str) -> set[str]:
    """Lowercase words (`{{placeholders}}` count as their name), without a few function words."""
    return {w for w in re.findall(r"[a-z0-9_]+", text.lower()) if w not in _STOP}


def similarity(a: str, b: str) -> float:
    """Token Jaccard of two wordings, 0 to 1."""
    x, y = tokens(a), tokens(b)
    return round(len(x & y) / len(x | y), 4) if x | y else 1.0


@dataclass(frozen=True)
class Link:
    rule_id: str
    previous_rule_id: str
    how: str  # suggestion | matched
    similarity: float


def match(old: list[dict[str, Any]], new: list[dict[str, Any]], known: dict[str, str] | None = None) -> list[Link]:
    """Old rules matched to new ones (each `{id, kind, index, text}`, one procedure's, in two releases). `known`: {old
    rule id: new rule id} from applied suggestions. Only changed rules are matched, one to one, within a kind."""
    new_ids, old_ids = {r["id"] for r in new}, {r["id"] for r in old}
    gone = {r["id"]: r for r in old if r["id"] not in new_ids}
    came = {r["id"]: r for r in new if r["id"] not in old_ids}
    out: list[Link] = []

    def take(o: dict[str, Any], n: dict[str, Any], how: str, score: float) -> None:
        out.append(Link(n["id"], o["id"], how, score))
        del gone[o["id"]], came[n["id"]]

    for oid, nid in (known or {}).items():
        o, n = gone.get(oid), came.get(nid)
        if o and n and o["kind"] == n["kind"]:
            take(o, n, "suggestion", similarity(o["text"], n["text"]))
    for o in sorted(gone.values(), key=lambda r: (r["kind"], r["index"])):
        n = next((r for r in came.values() if (r["kind"], r["index"]) == (o["kind"], o["index"])), None)
        if n is not None and (score := similarity(o["text"], n["text"])) >= SAME_POSITION_MIN:
            take(o, n, "matched", score)
    pairs = sorted(((similarity(o["text"], n["text"]), o, n) for o in gone.values() for n in came.values() if o["kind"] == n["kind"]),
                   key=lambda x: (-x[0], x[1]["index"], x[2]["index"], x[1]["id"], x[2]["id"]))
    for score, o, n in pairs:
        if score >= ANYWHERE_MIN and o["id"] in gone and n["id"] in came:
            take(o, n, "matched", score)
    return out


# --- what a release holds --------------------------------------------------------------------------------------------


@dataclass
class Snapshot:
    """One release's procedures: their rules and their file."""

    rules: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    files: dict[str, tuple[str, str]] = field(default_factory=dict)  # procedure id -> (path, content)


def snapshot(db: Session, sopc: Sopc, ws: str, number: int) -> Snapshot | None:
    """The procedures of a release, read with `sopc export` (an older format as `sopc migrate` converts it)."""
    rel = db.get(Release, (ws, number))
    if rel is None:
        return None
    files = {}
    for path, version in rel.files.items():
        content = db.exec(select(FileVersion.content).where(FileVersion.workspace == ws, FileVersion.path == path, FileVersion.version == version)).first()
        if content is not None:
            files[path] = content
    try:
        export = sopc.export(files)
    except Invalid:  # a release in an older sopc format
        try:
            export = sopc.export(sopc.migrate(files)["files"])
        except Invalid:
            return None
    out = Snapshot()
    for sop in export.get("sops") or []:
        out.rules[sop["id"]] = [{k: r[k] for k in ("id", "kind", "index", "text")} for r in sop.get("rules") or []]
        if sop.get("file") in files:
            out.files[sop["id"]] = (sop["file"], files[sop["file"]])
    return out


def _suggested(db: Session, ws: str, procedure_id: str, old: list[dict[str, Any]], new: list[dict[str, Any]], content: str | None) -> dict[str, str]:
    """{old rule id: new rule id} from applied suggestions whose edit is in the procedure's file in the new release (its
    suggested text, or that merged with other edits): the rule each one edited, and the new rule at its position (else the most similar new one of its kind)."""
    if content is None:
        return {}
    q = select(Suggestion).where(Suggestion.workspace_id == ws, Suggestion.procedure_id == procedure_id, Suggestion.status.in_(FIX_STATUSES))
    old_by_id = {r["id"]: r for r in old}
    new_ids = {r["id"] for r in new}
    came = [r for r in new if r["id"] not in old_by_id]
    out = {}
    for s in db.exec(q.order_by(Suggestion.created_at)):
        o = old_by_id.get(s.rule_id)
        if o is None or o["id"] in new_ids or not (s.new_text == content or (s.old_text is not None and s.new_text is not None and merge.contains(s.old_text, s.new_text, content))):
            continue
        same = [r for r in came if r["kind"] == o["kind"]]
        n = next((r for r in same if r["index"] == o["index"]), None) or max(same, key=lambda r: similarity(o["text"], r["text"]), default=None)
        if n is not None:
            out[o["id"]] = n["id"]
    return out


# --- reading the chains ----------------------------------------------------------------------------------------------


def lineages(db: Session, ws: str, procedure_id: str | None = None) -> dict[tuple[str, str], str]:
    """{(procedure id, rule id): lineage id} for every rule with a row; a rule without one is its own lineage."""
    q = select(RuleLineage.procedure_id, RuleLineage.rule_id, RuleLineage.lineage_id).where(RuleLineage.workspace_id == ws)
    if procedure_id:
        q = q.where(RuleLineage.procedure_id == procedure_id)
    return {(p, r): lin for p, r, lin in db.exec(q).all()}


def lineage_of(db: Session, ws: str, procedure_id: str, rule_id: str) -> str:
    found = db.exec(select(RuleLineage.lineage_id).where(RuleLineage.workspace_id == ws, RuleLineage.procedure_id == procedure_id, RuleLineage.rule_id == rule_id)).first()
    return found or rule_id


def members(db: Session, ws: str, procedure_id: str, lineage_id: str) -> list[str]:
    """Every rule id of a lineage, its first rule first."""
    rows = db.exec(select(RuleLineage.rule_id).where(RuleLineage.workspace_id == ws, RuleLineage.procedure_id == procedure_id, RuleLineage.lineage_id == lineage_id))
    return [lineage_id, *sorted(r for r in rows if r != lineage_id)]


def in_lineage(column: Any, ws: str, procedure_id: str, lineage_id: str) -> Any:
    """A condition: `column` (a rule id column) is one of the lineage's rule ids."""
    rows = select(RuleLineage.rule_id).where(RuleLineage.workspace_id == ws, RuleLineage.procedure_id == procedure_id, RuleLineage.lineage_id == lineage_id)
    return or_(column == lineage_id, column.in_(rows))


def expand(db: Session, ws: str, rule_id: str, procedure_id: str | None = None) -> tuple[set[str], set[str]]:
    """(lineage ids, rule ids) of the lineages `rule_id` is in, in any procedure unless one is given. The rule's own id
    is always among the lineage ids: it is one wherever it has no row."""
    q = select(RuleLineage.lineage_id).where(RuleLineage.workspace_id == ws, RuleLineage.rule_id == rule_id)
    rq = select(RuleLineage.rule_id).where(RuleLineage.workspace_id == ws)
    if procedure_id:
        q, rq = q.where(RuleLineage.procedure_id == procedure_id), rq.where(RuleLineage.procedure_id == procedure_id)
    lins = {rule_id, *db.exec(q).all()}
    return lins, lins | set(db.exec(rq.where(RuleLineage.lineage_id.in_(lins))).all())


def latest(db: Session, ws: str, procedure_id: str, lineage_id: str) -> tuple[str, int, str] | None:
    """(kind, index, text) of the lineage's rule as the most recent session ran it, or None before any did."""
    R, S = RuleResult, SessionRecord
    row = db.exec(select(R.kind, R.index, R.rule_text).join(S, S.id == R.session_id)
                  .where(R.workspace_id == ws, R.procedure_id == procedure_id, in_lineage(R.rule_id, ws, procedure_id, lineage_id))
                  .order_by(S.started_at.desc()).limit(1)).first()
    return (row[0], row[1], row[2]) if row else None


@dataclass
class Wording:
    rule_id: str
    kind: str
    index: int
    text: str
    text_hash: str
    first_seen: datetime | None
    last_seen: datetime | None
    results: int
    from_release: int | None

    def out(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "text": self.text, "from_release": self.from_release}


def wordings(db: Session, ws: str, procedure_id: str | None = None) -> dict[tuple[str, str], list[Wording]]:
    """{(procedure id, lineage id): its wordings seen in sessions, oldest first}. One per rule id: its text (sopc's id
    follows it), its kind and index at the last session that ran it, and `from_release`: the release that published
    it (its lineage row's), else the first release a session ran it on."""
    R, S, L = RuleResult, SessionRecord, RuleLineage
    q = (select(R.procedure_id, R.rule_id, R.kind, R.index, R.rule_text, R.rule_text_hash, func.min(S.started_at), func.max(S.started_at), func.count(R.id), func.min(S.release_id))
         .join(S, S.id == R.session_id).where(R.workspace_id == ws))
    if procedure_id:
        q = q.where(R.procedure_id == procedure_id)
    rows = db.exec(q.group_by(R.procedure_id, R.rule_id, R.kind, R.index, R.rule_text, R.rule_text_hash)).all()
    lq = select(L.procedure_id, L.rule_id, L.lineage_id, L.release_id).where(L.workspace_id == ws)
    links = {(p, r): (lin, rel) for p, r, lin, rel in db.exec(lq.where(L.procedure_id == procedure_id) if procedure_id else lq).all()}
    by_rule: dict[tuple[str, str], Wording] = {}
    for pid, rid, kind, index, text, h, first, last, n, rel in rows:
        first, last = _utc(first), _utc(last)
        w = by_rule.get((pid, rid))
        if w is None:
            by_rule[(pid, rid)] = Wording(rid, kind, index, text, h, first, last, n, links.get((pid, rid), (None, None))[1] or rel)
            continue
        if last and (w.last_seen is None or last > w.last_seen):
            w.kind, w.index, w.text, w.text_hash, w.last_seen = kind, index, text, h, last
        w.first_seen = min(x for x in (w.first_seen, first) if x) if (w.first_seen or first) else None
        w.results += n
        if links.get((pid, rid), (None, None))[1] is None and rel is not None:
            w.from_release = rel if w.from_release is None else min(w.from_release, rel)
    out: dict[tuple[str, str], list[Wording]] = defaultdict(list)
    for (pid, rid), w in by_rule.items():
        out[(pid, links.get((pid, rid), (rid, None))[0])].append(w)
    for group in out.values():
        group.sort(key=lambda w: (w.from_release is not None, w.from_release or 0, w.first_seen or datetime.min.replace(tzinfo=timezone.utc)))
    return dict(out)


def current(group: list[Wording]) -> Wording:
    """The wording sessions ran most recently."""
    return max(group, key=lambda w: (w.last_seen or datetime.min.replace(tzinfo=timezone.utc), w.from_release or 0))


# --- writing ---------------------------------------------------------------------------------------------------------


def record(db: Session, sopc: Sopc, ws: str, previous: int | None, number: int, follow: Follow | None = None) -> int:
    """Lineage for release `number` against `previous` (the release live before it). Rules that already have a row are
    left alone (a person's link or unlink, or a rule seen before). Returns how many links it wrote."""
    if previous is None or previous == number:
        return 0
    old, new = snapshot(db, sopc, ws, previous), snapshot(db, sopc, ws, number)
    if old is None or new is None:
        return 0
    rows = {(r.procedure_id, r.rule_id): r for r in db.exec(select(RuleLineage).where(RuleLineage.workspace_id == ws))}
    written, changed = 0, set()
    for pid in sorted(set(old.rules) & set(new.rules)):
        known = _suggested(db, ws, pid, old.rules[pid], new.rules[pid], new.files.get(pid, (None, None))[1])
        for link in match(old.rules[pid], new.rules[pid], known):
            if (pid, link.rule_id) in rows or link.rule_id in _ancestors(rows, pid, link.previous_rule_id):
                continue  # already decided, or the rule's text came back: it is still its own lineage's
            row = RuleLineage(workspace_id=ws, procedure_id=pid, rule_id=link.rule_id, previous_rule_id=link.previous_rule_id,
                              lineage_id=link.previous_rule_id, release_id=number, how=link.how, similarity=link.similarity, created_at=_now())
            db.add(row)
            rows[(pid, link.rule_id)] = row
            written += 1
            changed.add(pid)
    db.flush()
    for pid in sorted(changed):
        relineage(db, ws, pid)
        if follow:
            follow(db, ws, pid)
    return written


def rebuild(db: Session, sopc: Sopc, ws: str, follow: Follow | None = None) -> dict[str, Any]:
    """Recompute the matched and suggestion links of every release, each against the one before it by number (which
    release was live before each isn't kept); manual rows stay. Idempotent. {links, procedures}."""
    before = lineages(db, ws)
    db.exec(delete(RuleLineage).where(RuleLineage.workspace_id == ws, RuleLineage.how != "manual"))
    db.flush()
    numbers = sorted(db.exec(select(Release.number).where(Release.workspace == ws)).all())
    links = sum(record(db, sopc, ws, a, b) for a, b in zip(numbers, numbers[1:]))
    procs = {p for p, _r in before} | {p for p, _r in lineages(db, ws)}
    procs |= set(db.exec(select(RuleResult.procedure_id).where(RuleResult.workspace_id == ws).distinct()).all())
    for pid in sorted(procs):
        relineage(db, ws, pid)
        if follow:
            follow(db, ws, pid)
    return {"workspace": ws, "links": links, "procedures": len(procs)}


def rebuild_all(db: Session, sopc: Sopc, follow: Follow | None = None) -> list[dict[str, Any]]:
    return [rebuild(db, sopc, ws, follow) for ws in db.exec(select(Workspace.workspace).order_by(Workspace.workspace)).all()]


def relineage(db: Session, ws: str, procedure_id: str) -> None:
    """Set every row's `lineage_id` to the first rule of its chain (after a link anywhere in it changed)."""
    rows = {r.rule_id: r for r in db.exec(select(RuleLineage).where(RuleLineage.workspace_id == ws, RuleLineage.procedure_id == procedure_id))}
    for r in rows.values():
        root, seen = r.rule_id, {r.rule_id}
        while (row := rows.get(root)) is not None and row.previous_rule_id and row.previous_rule_id not in seen:
            root = row.previous_rule_id
            seen.add(root)
        if r.lineage_id != root:
            r.lineage_id = root
            db.add(r)
    db.flush()


def _ancestors(rows: dict[tuple[str, str], RuleLineage], procedure_id: str, rule_id: str | None) -> set[str]:
    """The rule and every rule before it in its chain."""
    out: set[str] = set()
    while rule_id and rule_id not in out:
        out.add(rule_id)
        row = rows.get((procedure_id, rule_id))
        rule_id = row.previous_rule_id if row else None
    return out


def link(db: Session, ws: str, procedure_id: str, rule_id: str, previous_rule_id: str | None, follow: Follow | None = None) -> RuleLineage:
    """A person's call: `rule_id` replaced `previous_rule_id`, or (None) it's a new rule. Replaces any row it had."""
    rows = {(r.procedure_id, r.rule_id): r for r in db.exec(select(RuleLineage).where(RuleLineage.workspace_id == ws, RuleLineage.procedure_id == procedure_id))}
    if previous_rule_id == rule_id:
        raise Conflict("a rule can't replace itself")
    if previous_rule_id and rule_id in _ancestors(rows, procedure_id, previous_rule_id):
        raise Conflict(f"'{previous_rule_id}' already comes after '{rule_id}'; unlink it first")
    row = rows.get((procedure_id, rule_id)) or RuleLineage(workspace_id=ws, procedure_id=procedure_id, rule_id=rule_id, lineage_id=rule_id, how="manual", created_at=_now())
    row.previous_rule_id, row.how, row.release_id, row.similarity, row.created_at = previous_rule_id, "manual", None, None, _now()
    row.lineage_id = previous_rule_id or rule_id
    db.add(row)
    db.flush()
    relineage(db, ws, procedure_id)
    if follow:
        follow(db, ws, procedure_id)
    return row


def chain(db: Session, ws: str, procedure_id: str, rule_id: str) -> dict[str, Any]:
    """A rule's lineage: its own row, the lineage id, and every wording sessions ran, oldest first."""
    row = db.get(RuleLineage, (ws, procedure_id, rule_id))
    lin = row.lineage_id if row else rule_id
    ids = members(db, ws, procedure_id, lin)
    seen = {w.rule_id: w for w in wordings(db, ws, procedure_id).get((procedure_id, lin), [])}
    rows = {r.rule_id: r for r in db.exec(select(RuleLineage).where(RuleLineage.workspace_id == ws, RuleLineage.procedure_id == procedure_id, RuleLineage.rule_id.in_(ids)))}
    rules = []
    for rid in ids:
        r, w = rows.get(rid), seen.get(rid)
        rules.append({"rule_id": rid, "previous_rule_id": r.previous_rule_id if r else None, "how": r.how if r else None, "similarity": r.similarity if r else None,
                      "release_id": r.release_id if r else None, "text": w.text if w else None, "from_release": w.from_release if w else (r.release_id if r else None)})
    rules.sort(key=lambda x: (x["from_release"] is not None, x["from_release"] or 0))
    return {"procedure_id": procedure_id, "rule_id": rule_id, "lineage_id": lin, "previous_rule_id": row.previous_rule_id if row else None,
            "how": row.how if row else None, "rules": rules}


class Lineage:
    """The API's side: a rule's lineage, linking and unlinking by hand, and rebuilding. `follow` runs after every
    change (`sopops_server.patterns.rekey`), in the same transaction."""

    def __init__(self, store: Store, follow: Follow):
        self.store = store
        self.follow = follow

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    def _procedure(self, db: Session, ws: str, rule_ids: list[str], procedure_id: str | None) -> str:
        """The procedure every rule id is in: the one given, else the only one that has them all (in a session's
        results, a lineage row or the live release)."""
        live = db.exec(select(Workspace.current_release).where(Workspace.workspace == ws)).first()
        snap = snapshot(db, self.store.sopc, ws, live) if live is not None else None
        procs = []
        for rid in rule_ids:
            found = set(db.exec(select(RuleResult.procedure_id).where(RuleResult.workspace_id == ws, RuleResult.rule_id == rid).distinct()).all())
            found |= set(db.exec(select(RuleLineage.procedure_id).where(RuleLineage.workspace_id == ws, or_(RuleLineage.rule_id == rid, RuleLineage.previous_rule_id == rid))).all())
            found |= {pid for pid, rules in (snap.rules if snap else {}).items() if any(r["id"] == rid for r in rules)}
            if procedure_id and procedure_id not in found:
                raise NotFound(f"no rule '{rid}' in procedure '{procedure_id}'")
            if not found:
                raise NotFound(f"no rule '{rid}' (no session ran it and the live release doesn't have it)")
            procs.append(found)
        if procedure_id:
            return procedure_id
        common = set.intersection(*procs)
        if not common:
            raise Conflict("the two rules aren't in the same procedure")
        if len(common) > 1:
            raise Conflict(f"rule '{rule_ids[0]}' is in more than one procedure ({', '.join(sorted(common))}); say which with procedure_id")
        return common.pop()

    def get(self, ws: str, rule_id: str, procedure_id: str | None = None) -> dict[str, Any]:
        with self._db() as db:
            return chain(db, ws, self._procedure(db, ws, [rule_id], procedure_id), rule_id)

    def link(self, ws: str, rule_id: str, previous_rule_id: str | None, procedure_id: str | None = None) -> dict[str, Any]:
        """`previous_rule_id` None: `rule_id` is a new rule (unlink)."""
        with self._db() as db, db.begin():
            pid = self._procedure(db, ws, [rule_id, *([previous_rule_id] if previous_rule_id else [])], procedure_id)
            link(db, ws, pid, rule_id, previous_rule_id, self.follow)
        with self._db() as db:
            return chain(db, ws, pid, rule_id)

    def rebuild(self, ws: str) -> dict[str, Any]:
        with self._db() as db, db.begin():
            return rebuild(db, self.store.sopc, ws, self.follow)


def _utc(at: datetime | None) -> datetime | None:
    if at is None:
        return None
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
