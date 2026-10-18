"""Suggestions: a procedure edit proposed from a pattern and its verdict, checked by compiling it, then applied.

- Made for a pattern whose verdict is `real`, `acceptable` or `sop_wrong` (`create`, then `generate` as a job, inline
  or on Hatchet). Status: `generating`, then `proposed` or `failed`; a proposal is then `applied`, `pr_open` or
  `dismissed`. A pull request is followed on GitHub (`sync_prs`, every `PR_CHECK_EVERY` at most per suggestion, by a
  periodic job and when it's read): merged makes it `merged`, closed without merging `dismissed` ("PR closed").
- The reasoning model sees the procedure's file at head, the rule, the pattern, the verdict and its note, and up to 5
  of the pattern's findings (`sopops.ai.programs.SuggestEdits`). The edited file must compile with the rest of head;
  if it doesn't, it's sent back once with sopc's issues, else the suggestion fails with them. `plan` records which
  agents' prompts change and how (a unified diff per agent).
- **Apply**: with a repo connection (`sopops.github`), a pull request with the file; without one, a draft edit of the
  file, as if saved in the editor, so it shows in Unpublished changes and goes out with the next publish. Either way
  only while the file at head is still the one the suggestion was made from.
"""

from __future__ import annotations

import difflib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from . import forms, github, lineage
from .ai import lm as ai_lm
from .ai.programs import SuggestEdits
from .ai.signatures import VERSIONS, ExampleIn
from .errors import Conflict, NotFound
from .orm import Pattern, Suggestion, Verdict, Workspace
from .patterns import KIND_LABEL
from .sopc import Invalid
from .verdicts import SUGGESTIBLE, release_with, suggestion_summary, verdict_out

if TYPE_CHECKING:
    from .ai.lm import AI
    from .store import Store

log = logging.getLogger("sopops.suggestions")

PR_CHECK_EVERY = timedelta(minutes=1)  # a pull request's state is read from GitHub at most this often per suggestion


def _now() -> datetime:
    return datetime.now(timezone.utc)


def prompt_plan(before: dict[str, dict], after: dict[str, dict]) -> dict[str, Any]:
    """Compiled agents before and after: the agents whose prompt changes, each with a unified diff and line counts."""
    agents = []
    for a in sorted(set(before) | set(after)):
        old, new = (before.get(a) or {}).get("prompt", ""), (after.get(a) or {}).get("prompt", "")
        if old == new:
            continue
        diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"{a} (now)", f"{a} (suggested)", n=2, lineterm=""))
        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        agents.append({"agent": a, "added": added, "removed": removed, "diff": "\n".join(diff)})
    return {"agents": agents}


class Suggestions:
    def __init__(self, store: Store):
        self.store = store

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    def _row(self, db: Session, ws: str, suggestion_id: str) -> Suggestion:
        s = db.get(Suggestion, suggestion_id)
        if s is None or s.workspace_id != ws:
            raise NotFound(f"no suggestion '{suggestion_id}'")
        return s

    def _out(self, db: Session, s: Suggestion) -> dict[str, Any]:
        p = db.get(Pattern, s.pattern_id)
        v = db.get(Verdict, s.verdict_id) if s.verdict_id else None
        return {
            **suggestion_summary(s, p.name if p else None, release_with(db, s.workspace_id, s)), "path": s.path, "base_version": s.base_version, "old_text": s.old_text, "new_text": s.new_text,
            "plan": s.plan, "note": s.note, "reviewer": s.reviewer, "attempts": s.attempts, "model": s.model, "prompt_version": s.prompt_version,
            "pattern": {"pattern_id": p.id, "kind": p.kind, "name": p.name, "description": _description(db, p), "procedure_id": p.procedure_id, "rule_id": p.rule_id} if p else None,
            "verdict": verdict_out(v) if v else None,
        }

    # --- reads ---------------------------------------------------------------------------------------------------

    def get(self, ws: str, suggestion_id: str) -> dict[str, Any]:
        """A `pr_open` suggestion's pull request is checked first (when it wasn't in the last `PR_CHECK_EVERY`)."""
        with self._db() as db:
            s = self._row(db, ws, suggestion_id)
        if s.status == "pr_open":
            self._sync_pr(s)
        with self._db() as db:
            return self._out(db, self._row(db, ws, suggestion_id))

    def sync_prs(self, ws: str | None = None, force: bool = False) -> dict[str, int]:
        """Check every open pull request (of one workspace, or all): {merged, closed, open, failed}."""
        with self._db() as db:
            q = select(Suggestion).where(Suggestion.status == "pr_open", Suggestion.pr_url.is_not(None))
            rows = list(db.exec(q.where(Suggestion.workspace_id == ws) if ws else q))
        out = {"merged": 0, "closed": 0, "open": 0, "failed": 0}
        for s in rows:
            out[self._sync_pr(s, force) or "failed"] += 1
        return out

    def _sync_pr(self, s: Suggestion, force: bool = False) -> str | None:
        """The pull request's state, applied to the suggestion; None when GitHub couldn't say (logged)."""
        now = _now()
        checked = _utc(s.pr_checked_at)
        if not force and checked is not None and now - checked < PR_CHECK_EVERY:
            return "open"  # checked a moment ago
        try:
            state = github.pr_status(self.store, s.workspace_id, s.pr_url or "")
        except github.GitHubError as e:
            log.warning("checking %s for suggestion %s failed: %s", s.pr_url, s.id, e)
            self._save(s, pr_checked_at=now)
            return None
        if state == "merged":
            self._save(s, status="merged", pr_checked_at=now, merged_at=now)
        elif state == "closed":
            self._save(s, status="dismissed", note="PR closed", pr_checked_at=now)
        else:
            self._save(s, pr_checked_at=now)
        return state

    def list(self, ws: str, status: str | None = None) -> list[dict[str, Any]]:
        """Newest first."""
        with self._db() as db:
            q = select(Suggestion, Pattern.name).join(Pattern, Pattern.id == Suggestion.pattern_id, isouter=True).where(Suggestion.workspace_id == ws)
            if status:
                q = q.where(Suggestion.status == status)
            return [suggestion_summary(s, name, release_with(db, ws, s)) for s, name in db.exec(q.order_by(Suggestion.created_at.desc()))]

    # --- writes --------------------------------------------------------------------------------------------------

    def create(self, ws: str, pattern_id: str) -> dict[str, Any]:
        """A suggestion waiting to be generated (`generate`), for the pattern's current verdict."""
        p, v = self.store.verdicts.effective_verdict(ws, pattern_id)
        if v is None or v.verdict not in SUGGESTIBLE:
            raise Conflict("a suggestion needs a verdict of real, acceptable or sop_wrong")
        now = _now()
        with self._db() as db, db.begin():
            s = Suggestion(id=str(uuid.uuid4()), workspace_id=ws, pattern_id=p.id, verdict_id=v.id, procedure_id=p.procedure_id, rule_id=p.rule_id,
                           status="generating", created_at=now, updated_at=now)
            db.add(s)
        with self._db() as db:
            return self._out(db, s)

    def _save(self, s: Suggestion, **fields: Any) -> dict[str, Any]:
        with self._db() as db, db.begin():
            row = self._row(db, s.workspace_id, s.id)
            for k, v in {**fields, "updated_at": _now()}.items():
                setattr(row, k, v)
            db.add(row)
        with self._db() as db:
            return self._out(db, row)

    def generate(self, ws: str, suggestion_id: str, ai: AI | None = None) -> dict[str, Any]:
        """Ask the reasoning model for the edit and check it. Does nothing unless the suggestion is `generating`."""
        with self._db() as db:
            s = self._row(db, ws, suggestion_id)
            v = db.get(Verdict, s.verdict_id) if s.verdict_id else None
        if s.status != "generating":
            return self.get(ws, suggestion_id)
        try:
            return self._generate(ws, s, v, ai or ai_lm.get_ai())
        except Exception as e:  # a model or sopc outage: the suggestion fails with the reason, and can be made again
            log.exception("generating suggestion %s failed", s.id)
            return self._save(s, status="failed", error=f"{type(e).__name__}: {str(e)[:1000]}")

    def _generate(self, ws: str, s: Suggestion, v: Verdict | None, ai: AI) -> dict[str, Any]:
        if v is None or v.verdict not in SUGGESTIBLE:
            return self._save(s, status="failed", error="The pattern's verdict doesn't call for an edit.")
        head = {f["path"]: f for f in self.store.head(ws, content=True)}
        files = {path: f["content"] for path, f in head.items()}
        proc = next((x for x in self.store.items(ws)["procedures"] if x["id"] == s.procedure_id), None)
        if proc is None:
            return self._save(s, status="failed", error=f"The procedure '{s.procedure_id}' isn't in the workspace any more.")
        path = proc["file"]
        sop = next((x for x in self.store.sopc.export(files)["sops"] if x["id"] == s.procedure_id), {})
        rule = self._current_rule(ws, s, sop.get("rules") or [])
        if rule is None:
            return self._save(s, status="failed", error="The rule isn't in the procedure any more.")
        p, examples = self.store.verdicts.examples(ws, s.pattern_id)
        old = files[path]
        s.path, s.base_version, s.old_text, s.rule_id = path, head[path]["version"], old, rule["id"]
        self._save(s, path=path, base_version=s.base_version, old_text=old, rule_id=rule["id"])

        def validate(block_id: str, text: str) -> str | None:
            if block_id != s.procedure_id:
                return f"block_id must be '{s.procedure_id}', the procedure's id."
            if text.strip() == old.strip():
                return "The file is unchanged."
            compiled = self.store.sopc.compile({**files, path: _eol(text)})
            if not compiled.valid:
                return "; ".join(forms.locate(i).text or i.message for i in compiled.issues if i.severity == "error")
            try:
                found = forms.find(forms.from_export(self.store.sopc.export({**files, path: _eol(text)})), "procedure", s.procedure_id)
            except Invalid as e:
                return str(e)
            return None if found is not None else f"The file must still define the procedure '{s.procedure_id}'."

        got = SuggestEdits(ai, validate)(
            procedure_id=s.procedure_id, procedure_file=old, rule=f"{KIND_LABEL.get(rule['kind'], rule['kind'])} {rule['index']}: {rule['text']}",
            pattern=p.name if p.kind == "rule" else f"{p.name}. {p.description or ''}".strip(), verdict=v.verdict, note=v.note or "",
            examples=[ExampleIn(status=e["status"], reason=e["reason"] or "", quotes=[str(x.get("quote")) for x in e["evidence"] if x.get("quote")][:3])
                      for e in examples],
        )
        common = {"new_text": _eol(got.new_text), "rationale": got.rationale, "attempts": got.attempts, "model": ai.model("reasoning"), "prompt_version": VERSIONS["suggest"]}
        if got.error:
            return self._save(s, status="failed", error=got.error, **common)
        plan = prompt_plan(self.store.sopc.compile(files).agents, self.store.sopc.compile({**files, path: common["new_text"]}).agents)
        return self._save(s, status="proposed", error=None, plan=plan, **common)

    def _current_rule(self, ws: str, s: Suggestion, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The rule the suggestion is about, as it reads at head: the rule of the pattern's lineage (`sopops.lineage`).
        A rewording that isn't published yet has no lineage row: the live release's rule of the lineage, matched to
        head's rules the way publishing will match it."""
        with self._db() as db:
            lid = lineage.lineage_of(db, ws, s.procedure_id, s.rule_id)
            ids = lineage.members(db, ws, s.procedure_id, lid)
            found = next((r for r in rules if r["id"] in ids), None)
            live = db.exec(select(Workspace.current_release).where(Workspace.workspace == ws)).first()
            snap = lineage.snapshot(db, self.store.sopc, ws, live) if found is None and live is not None else None
        if found is not None or snap is None:
            return found
        released = snap.rules.get(s.procedure_id, [])
        was = next((r for r in released if r["id"] in ids), None)
        moved = {x.previous_rule_id: x.rule_id for x in lineage.match(released, rules)}
        return next((r for r in rules if was and r["id"] == moved.get(was["id"])), None)

    def apply(self, ws: str, suggestion_id: str, reviewer: str | None = None) -> dict[str, Any]:
        """A pull request when the workspace has a repo connection, else a draft edit of the procedure's file."""
        with self._db() as db:
            s = self._row(db, ws, suggestion_id)
            p = db.get(Pattern, s.pattern_id)
        if s.status != "proposed":
            raise Conflict(f"only a proposed suggestion can be applied; this one is {s.status}")
        assert s.path is not None and s.new_text is not None
        try:
            current = self.store.file(ws, s.path)["content"]
        except NotFound:
            current = None
        if current != s.old_text:
            raise Conflict("the procedure changed since this suggestion was made; make a new one")
        repo = github.repo_connection(self.store, ws)
        name = p.name if p else s.pattern_id
        if repo is not None:
            agents = [a["agent"] for a in (s.plan or {}).get("agents", [])]
            body = "\n\n".join(x for x in (s.rationale, f"Pattern: {name}", f"Agents whose prompt changes: {', '.join(agents) or 'none'}", "Suggested by sopops.") if x)
            url = github.open_pull_request(self.store, ws, {s.path: s.new_text}, f"{s.procedure_id}: {name}", body)
            return self._save(s, status="pr_open", pr_url=url, reviewer=reviewer or s.reviewer, pr_opened_at=_now())
        self.store.edit(ws, {s.path: s.new_text}, author=reviewer or "sopops", note=f"Suggestion for “{name}”")
        return self._save(s, status="applied", reviewer=reviewer or s.reviewer, applied_at=_now())

    def dismiss(self, ws: str, suggestion_id: str, note: str | None = None, reviewer: str | None = None) -> dict[str, Any]:
        with self._db() as db:
            s = self._row(db, ws, suggestion_id)
        if s.status in ("applied", "pr_open", "merged"):
            raise Conflict(f"this suggestion is already {s.status}")
        return self._save(s, status="dismissed", note=note or None, reviewer=reviewer or s.reviewer)


def _utc(at: datetime | None) -> datetime | None:
    if at is None:
        return None
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _eol(text: str) -> str:
    return text.rstrip("\n") + "\n"


def _description(db: Session, p: Pattern) -> str | None:
    """A rule pattern's description is its rule as it reads now; a failure mode's is the model's."""
    if p.kind != "rule":
        return p.description
    now = lineage.latest(db, p.workspace_id, p.procedure_id, p.rule_id)
    return now[2] if now else p.description
