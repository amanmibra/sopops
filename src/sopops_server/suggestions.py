"""Suggestions: a procedure edit proposed from a pattern and its verdict, checked by compiling it, then applied.

- Made for a pattern whose verdict is `real`, `acceptable` or `sop_wrong` (`create`, then `generate` as a job, inline
  or on Hatchet). Status: `generating`, then `proposed` or `failed`; a proposal is then `applied`, `pr_open` or
  `dismissed`. A pull request is followed on GitHub (`sync_prs`, every `PR_CHECK_EVERY` at most per suggestion, by a
  periodic job and when it's read; backed off while the repository sends webhooks), and right away when the
  `pull_request` webhook says it closed (`pr_closed`): merged makes it `merged`, closed without merging `dismissed`
  ("PR closed").
- The reasoning model sees the procedure's file at head, the rule, the pattern, the verdict and its note, and up to 5
  of the pattern's findings (`sopops_server.ai.programs.SuggestEdits`). The edited file must compile with the rest of head;
  if it doesn't, it's sent back once with sopc's issues, else the suggestion fails with them. `plan` records which
  agents' prompts change and how (a unified diff per agent).
- **Apply**: with a repo connection (`sopops_server.github`), a pull request with the file; without one, a draft edit of the
  file, as if saved in the editor, so it shows in Unpublished changes and goes out with the next publish.
- **Since it was written**: when the file at head isn't the one the suggestion was made from (another suggestion was
  applied, or someone edited it), the edit is merged into it (`sopops_server.merge`, a three-way line merge) and the result
  checked like a proposal: it must compile with the rest of head and still define the procedure. `since_written`,
  computed when a proposed suggestion is read: `current` (unchanged), `merges` (applying writes the merged file and
  records `merged_into_version`) or `conflict` (the same lines were changed, or the merged file doesn't compile; apply
  is refused).
- **Rewrite**: a conflicting suggestion is written again as a new one (`rewrite`, then `generate`), from the file as it
  is now, with the edits made since the old one was written given to the model to keep. The old one is dismissed
  ("Rewritten against the current text") and the new one's `rewrite_of` points to it. A new row, not the old one
  regenerated in place: a suggestion is one proposal against one base, so the history (and what was dismissed) stays
  as it was, and generating is the same job as for any suggestion.
"""

from __future__ import annotations

import difflib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

from . import forms, github, lineage, merge
from .ai import spend
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
REWRITTEN = "Rewritten against the current text."


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


def changes(path: str, before: str, after: str) -> str:
    """A unified diff of the procedure's file, as written against to now."""
    return "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), f"{path} (when written)", f"{path} (now)", n=2, lineterm=""))


class Suggestions:
    def __init__(self, store: Store):
        self.store = store
        self._fits: dict[tuple[str, str, tuple[tuple[str, int], ...]], dict[str, Any]] = {}  # since_written by suggestion and head

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    def _row(self, db: Session, ws: str, suggestion_id: str) -> Suggestion:
        s = db.get(Suggestion, suggestion_id)
        if s is None or s.workspace_id != ws:
            raise NotFound(f"no suggestion '{suggestion_id}'")
        return s

    def _out(self, db: Session, s: Suggestion, since: dict[str, Any] | None = None) -> dict[str, Any]:
        p = db.get(Pattern, s.pattern_id)
        v = db.get(Verdict, s.verdict_id) if s.verdict_id else None
        after = db.exec(select(Suggestion.id).where(Suggestion.workspace_id == s.workspace_id, Suggestion.rewrite_of == s.id).order_by(Suggestion.created_at.desc())).first()
        return {
            **suggestion_summary(s, p.name if p else None, release_with(db, s.workspace_id, s)), "path": s.path, "base_version": s.base_version, "old_text": s.old_text, "new_text": s.new_text,
            "plan": s.plan, "note": s.note, "reviewer": s.reviewer, "attempts": s.attempts, "model": s.model, "prompt_version": s.prompt_version,
            "pattern": {"pattern_id": p.id, "kind": p.kind, "name": p.name, "description": _description(db, p), "procedure_id": p.procedure_id, "rule_id": p.rule_id} if p else None,
            "verdict": verdict_out(v) if v else None, "merged_into_version": s.merged_into_version, "rewrite_of": s.rewrite_of, "rewritten_as": after,
            "freshness": since["state"] if since else None, "since_written": since,
        }

    # --- since it was written ------------------------------------------------------------------------------------

    def _issues(self, files: dict[str, str], path: str, procedure_id: str, text: str) -> str | None:
        """Why `text` can't be the procedure's file with the rest of `files`: sopc's errors, or the procedure gone."""
        changed = {**files, path: _eol(text)}
        compiled = self.store.sopc.compile(changed)
        if not compiled.valid:
            return "; ".join(forms.locate(i).text or i.message for i in compiled.issues if i.severity == "error")
        try:
            found = forms.find(forms.from_export(self.store.sopc.export(changed)), "procedure", procedure_id)
        except Invalid as e:
            return str(e)
        return None if found is not None else f"The file must still define the procedure '{procedure_id}'."

    def _since(self, s: Suggestion, head: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        """How a proposed suggestion fits the procedure's file at head (`head`: path -> {version, content}): `current`,
        `merges` (with the merged file) or `conflict` (with the conflicting parts, or sopc's issues). None for any
        other suggestion. Kept per suggestion and head versions, so reading it again doesn't compile again."""
        if s.status != "proposed" or not s.path or s.old_text is None or s.new_text is None:
            return None
        now = head.get(s.path)
        out: dict[str, Any] = {"state": "current", "head_version": now["version"] if now else None, "changes": None, "merged_text": None, "conflicts": [], "issues": None}
        if now is not None and now["content"] == s.old_text:
            return out
        if now is None:
            return {**out, "state": "conflict", "issues": f"The procedure's file {s.path} isn't in the workspace any more."}
        key = (s.workspace_id, s.id, tuple(sorted((p, f["version"]) for p, f in head.items())))
        if key in self._fits:
            return self._fits[key]
        out["changes"] = changes(s.path, s.old_text, now["content"])
        got = merge.merge(s.old_text, now["content"], s.new_text)
        if not got.clean:
            out |= {"state": "conflict", "conflicts": [{"line": c.line, "base": c.base, "current": c.ours, "suggested": c.theirs} for c in got.conflicts]}
        else:
            assert got.text is not None
            issues = self._issues({p: f["content"] for p, f in head.items()}, s.path, s.procedure_id, got.text)
            out |= {"state": "conflict", "issues": issues} if issues else {"state": "merges", "merged_text": _eol(got.text)}
        if len(self._fits) > 512:
            self._fits.clear()
        self._fits[key] = out
        return out

    def _head(self, ws: str) -> dict[str, dict[str, Any]]:
        return {f["path"]: f for f in self.store.head(ws, content=True)}

    def annotate(self, ws: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Set `freshness` on suggestion summaries that are proposed (head is read once, and only if there's one)."""
        ids = [x["suggestion_id"] for x in items if x and x.get("status") == "proposed"]
        if not ids:
            return items
        head = self._head(ws)
        with self._db() as db:
            rows = {s.id: s for s in db.exec(select(Suggestion).where(Suggestion.workspace_id == ws, Suggestion.id.in_(ids)))}
        for x in items:
            if x and x["suggestion_id"] in rows:
                since = self._since(rows[x["suggestion_id"]], head)
                x["freshness"] = since["state"] if since else None
        return items

    # --- reads ---------------------------------------------------------------------------------------------------

    def get(self, ws: str, suggestion_id: str) -> dict[str, Any]:
        """A `pr_open` suggestion's pull request is checked first (when it wasn't in the last `PR_CHECK_EVERY`)."""
        with self._db() as db:
            s = self._row(db, ws, suggestion_id)
        if s.status == "pr_open":
            self._sync_pr(s)
        with self._db() as db:
            s = self._row(db, ws, suggestion_id)
        since = self._since(s, self._head(ws)) if s.status == "proposed" else None
        with self._db() as db:
            return self._out(db, s, since)

    def sync_prs(self, ws: str | None = None, force: bool = False) -> dict[str, int]:
        """Check every open pull request (of one workspace, or all): {merged, closed, open, failed}."""
        with self._db() as db:
            q = select(Suggestion).where(Suggestion.status == "pr_open", Suggestion.pr_url.is_not(None))
            rows = list(db.exec(q.where(Suggestion.workspace_id == ws) if ws else q))
        out = {"merged": 0, "closed": 0, "open": 0, "failed": 0}
        for s in rows:
            out[self._sync_pr(s, force) or "failed"] += 1
        return out

    def pr_closed(self, workspaces: list[str], url: str, state: str) -> int:
        """A webhook said the pull request at `url` merged or closed: apply it to its open suggestions, as polling
        would. Returns how many."""
        with self._db() as db:
            rows = list(db.exec(select(Suggestion).where(Suggestion.status == "pr_open", Suggestion.pr_url == url, Suggestion.workspace_id.in_(workspaces))))
        for s in rows:
            self._sync_pr(s, force=True, state=state)
        return len(rows)

    def _sync_pr(self, s: Suggestion, force: bool = False, state: str | None = None) -> str | None:
        """The pull request's state (`state` when a webhook said it, else read from GitHub), applied to the
        suggestion; None when GitHub couldn't say (logged)."""
        now = _now()
        if state is None:
            checked = _utc(s.pr_checked_at)
            every = github.check_every(github.repo_connection(self.store, s.workspace_id), PR_CHECK_EVERY)
            if not force and checked is not None and now - checked < every:
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
            items = [suggestion_summary(s, name, release_with(db, ws, s)) for s, name in db.exec(q.order_by(Suggestion.created_at.desc()))]
        return self.annotate(ws, items)

    # --- writes --------------------------------------------------------------------------------------------------

    def create(self, ws: str, pattern_id: str, rewrite_of: str | None = None) -> dict[str, Any]:
        """A suggestion waiting to be generated (`generate`), for the pattern's current verdict."""
        p, v = self.store.verdicts.effective_verdict(ws, pattern_id)
        if v is None or v.verdict not in SUGGESTIBLE:
            raise Conflict("a suggestion needs a verdict of real, acceptable or sop_wrong")
        now = _now()
        with self._db() as db, db.begin():
            s = Suggestion(id=str(uuid.uuid4()), workspace_id=ws, pattern_id=p.id, verdict_id=v.id, procedure_id=p.procedure_id, rule_id=p.rule_id,
                           status="generating", rewrite_of=rewrite_of, created_at=now, updated_at=now)
            db.add(s)
        with self._db() as db:
            return self._out(db, s)

    def rewrite(self, ws: str, suggestion_id: str, reviewer: str | None = None) -> dict[str, Any]:
        """A new suggestion for the same pattern, to be generated (`generate`) against the procedure's file as it is
        now, keeping the edits made since this one was written. This one is dismissed, pointing to it."""
        with self._db() as db:
            old = self._row(db, ws, suggestion_id)
        if old.status != "proposed":
            raise Conflict(f"only a proposed suggestion can be rewritten; this one is {old.status}")
        new = self.create(ws, old.pattern_id, rewrite_of=old.id)
        self._save(old, status="dismissed", note=REWRITTEN, reviewer=reviewer or old.reviewer)
        return new

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
            before = db.get(Suggestion, s.rewrite_of) if s.rewrite_of else None
        if s.status != "generating":
            return self.get(ws, suggestion_id)
        try:
            with spend.scope(self.store.engine, ws, "suggestion"):
                return self._generate(ws, s, v, ai or self.store.ai_keys.ai(ws), before)
        except Exception as e:  # a model or sopc outage: the suggestion fails with the reason, and can be made again
            log.exception("generating suggestion %s failed", s.id)
            return self._save(s, status="failed", error=f"{type(e).__name__}: {str(e)[:1000]}")

    def _generate(self, ws: str, s: Suggestion, v: Verdict | None, ai: AI, before: Suggestion | None = None) -> dict[str, Any]:
        """`before`: the suggestion this one rewrites; the edits to the file since it was written go to the model."""
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
            return self._issues(files, path, s.procedure_id, text)

        since = changes(path, before.old_text, old) if before is not None and before.old_text is not None and before.path == path and before.old_text != old else ""

        got = SuggestEdits(ai, validate)(
            procedure_id=s.procedure_id, procedure_file=old, rule=f"{KIND_LABEL.get(rule['kind'], rule['kind'])} {rule['index']}: {rule['text']}",
            pattern=p.name if p.kind == "rule" else f"{p.name}. {p.description or ''}".strip(), verdict=v.verdict, note=v.note or "",
            examples=[ExampleIn(status=e["status"], reason=e["reason"] or "", quotes=[str(x.get("quote")) for x in e["evidence"] if x.get("quote")][:3])
                      for e in examples],
            changes_since=since,
        )
        common = {"new_text": _eol(got.new_text), "rationale": got.rationale, "attempts": got.attempts, "model": ai.model("reasoning"), "prompt_version": VERSIONS["suggest"]}
        if got.error:
            return self._save(s, status="failed", error=got.error, **common)
        plan = prompt_plan(self.store.sopc.compile(files).agents, self.store.sopc.compile({**files, path: common["new_text"]}).agents)
        return self._save(s, status="proposed", error=None, plan=plan, **common)

    def _current_rule(self, ws: str, s: Suggestion, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The rule the suggestion is about, as it reads at head: the rule of the pattern's lineage (`sopops_server.lineage`).
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
        since = self._since(s, self._head(ws))
        assert since is not None
        if since["state"] == "conflict":
            why = (f"the merged file doesn't compile: {since['issues']}" if since["issues"] and not since["conflicts"] else
                   since["issues"] or ("the same part of the file was changed" if len(since["conflicts"]) == 1 else f"{len(since['conflicts'])} parts of the file were changed both ways"))
            raise Conflict(f"the procedure changed since this suggestion was written, and the two don't merge ({why}). Rewrite it against the current text.")
        text, merged = (since["merged_text"], since["head_version"]) if since["state"] == "merges" else (s.new_text, None)
        repo = github.repo_connection(self.store, ws)
        name = p.name if p else s.pattern_id
        if repo is not None:
            agents = [a["agent"] for a in (s.plan or {}).get("agents", [])]
            body = "\n\n".join(x for x in (s.rationale, f"Pattern: {name}", f"Agents whose instructions change: {', '.join(agents) or 'none'}",
                                            "Merged with changes made to the file since it was written." if merged else None, "Suggested by sopops.") if x)
            url = github.open_pull_request(self.store, ws, {s.path: text}, f"{s.procedure_id}: {name}", body)
            return self._save(s, status="pr_open", pr_url=url, reviewer=reviewer or s.reviewer, pr_opened_at=_now(), merged_into_version=merged)
        written = self.store.edit(ws, {s.path: text}, author=reviewer or "sopops", note=f"Suggestion for “{name}”" + (" (merged with changes made since)" if merged else ""))
        version = next((w["version"] for w in written if w["path"] == s.path), None)
        return self._save(s, status="applied", reviewer=reviewer or s.reviewer, applied_at=_now(), merged_into_version=merged, applied_version=version)

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
