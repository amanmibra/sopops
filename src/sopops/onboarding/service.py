"""Imports: running an extraction here, onboarding tokens for coding agents, review, and applying a proposal as drafts.

Two ways in, one way out:

- **LLM import** (`source: llm`): prompts pasted, uploaded or read from ElevenLabs/Vapi are extracted here, in a
  background thread, with the configured models (`sopops.ai`).
- **Agent import** (`source: agent`): the user starts Claude Code or Codex in their repo with the `sopops-mcp` server
  and an onboarding token. The agent reports progress and pushes a proposal; each push replaces the last one. In repo
  mode the workspace's GitHub connection is checked first and its folder read: without a sopc project sopc can build,
  the agent also opens the onboarding pull request adding one, which sopops follows (`pulls`) and pulls when merged.
- **sopc project** (`source: sopc`): a team that already keeps one (or exports one from wherever its prompts live)
  uploads its files or reads them from the workspace's GitHub repository, optionally with each agent's original prompt.
  It's checked like any proposal; applying writes exactly its files. Its content isn't edited here: change the
  project and import it again.

Either way the user reviews the proposal (edits, drift decisions, fidelity) and applies it: its blocks, groups and
agents are written as drafts, for the usual Unpublished changes review and publish.

Onboarding tokens are `tokens` rows tied to the import (see `sopops.tokens`): single-workspace, scoped to `onboarding:write` (push a proposal, post progress: nothing else),
stored as a sha256 hash, shown once, and valid until they expire (2 hours), are revoked, or the import is applied or
discarded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlmodel import Session, inspect, select

from .. import forms, github
from ..ai import spend
from ..ai.lm import AI, RULES, AIError, configured, get_ai, model_for
from ..errors import workspace_id
from ..orm import ImportEvent, ImportRecord, Token
from ..sopc import Invalid, SopcUnavailable
from ..store import Conflict, NotFound, Store
from ..tokens import Forbidden, Tokens, Unauthorized
from ..tokens import out as token_info
from . import fidelity, kickoff, project, pulls, summary
from .pipeline import extract
from .review import refresh_used_by, resolve
from .rules import RulesModels
from .types import Finding, Proposal, ProposalContent, SourcePrompt

log = logging.getLogger("sopops.onboarding")

SCOPE = "onboarding:write"
TOKEN_PREFIX = "sopops_onb_"
TOKEN_TTL = timedelta(hours=2)
DONE = ("applied", "discarded")


def ai_from_env() -> AI | None:
    """The configured models, the rules mode (`SOPOPS_MODEL_REASONING=rules`, for trying it out), or None."""
    if model_for("reasoning") == RULES:
        return RulesModels()
    ai = get_ai()
    return ai if configured(ai) else None


class Onboarding:
    def __init__(self, store: Store, ai: AI | None | Callable[[], AI | None] = ai_from_env):
        self.store = store
        self._ai = ai
        self._overlaps: dict[str, dict] = {}
        self._overlap_lock = threading.Lock()
        self._fail_interrupted()

    @property
    def ai(self) -> AI | None:
        return self._ai() if callable(self._ai) and not hasattr(self._ai, "predict") else self._ai  # type: ignore[return-value]

    @ai.setter
    def ai(self, value: AI | None) -> None:
        self._ai = value

    @property
    def tokens(self) -> Tokens:
        return self.store.tokens

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    def _fail_interrupted(self) -> None:
        if "imports" not in inspect(self.store.engine).get_table_names():
            return  # a Store made without migrations (e.g. to print the OpenAPI spec)
        with self._db() as db:
            for r in db.exec(select(ImportRecord).where(ImportRecord.status == "running", ImportRecord.source == "llm")):
                r.status, r.error, r.updated_at = "failed", "sopops restarted while this import was running; start it again", _now()
                db.add(r)
            db.commit()

    # --- reading ------------------------------------------------------------

    def list(self, workspace: str) -> list[dict]:
        with self._db() as db:
            rows = db.exec(select(ImportRecord).where(ImportRecord.workspace == workspace).order_by(ImportRecord.created_at.desc()).limit(50))
            return [out(r, self.tokens.for_import(r.id, db), proposal=False) for r in rows]

    def get(self, workspace: str, id: str) -> dict:
        """An open onboarding pull request is checked first (when it wasn't in the last `pulls.PR_CHECK_EVERY`)."""
        with self._db() as db:
            r = self._load(db, workspace, id)
        if r.pr_state == "open":
            pulls.sync_one(self.store, r.id)
        with self._db() as db:
            r = self._load(db, workspace, id)
            return out(r, self.tokens.for_import(r.id, db))

    def events(self, workspace: str, id: str, after: int = 0) -> list[dict]:
        with self._db() as db:
            self._load(db, workspace, id)
            rows = db.exec(select(ImportEvent).where(ImportEvent.import_id == id, ImportEvent.id > after).order_by(ImportEvent.id))
            return [event_out(e) for e in rows]

    def record_of(self, workspace: str, id: str) -> ImportRecord:
        with self._db() as db:
            return self._load(db, workspace, id)

    def _load(self, db: Session, workspace: str, id: str) -> ImportRecord:
        r = db.get(ImportRecord, id)
        if r is None or r.workspace != workspace:
            raise NotFound(f"import '{id}' not found")
        return r

    # --- LLM imports ----------------------------------------------------------

    def start(self, workspace: str, sources: list[SourcePrompt], author: str | None = None, run: bool = True) -> dict:
        """Start extracting in a background thread. Raises LLMError if no model is configured."""
        if self.ai is None:
            raise AIError("No model is configured for extraction. Set SOPOPS_MODEL_REASONING (and optionally SOPOPS_MODEL_FAST) "
                           "to a LiteLLM model name and its provider key, or use Claude Code / Codex instead.")
        if not sources:
            raise ValueError("add at least one prompt")
        names = [s.name for s in sources]
        rec = ImportRecord(id=str(uuid.uuid4()), workspace=workspace_id(workspace), source="llm", status="running", agents=names,
                           created_at=_now(), updated_at=_now(), created_by=author)
        with self._db() as db:
            db.add(rec)
            db.commit()
        self.event(rec.id, "start", f"Importing {len(sources)} agent{'s' if len(sources) != 1 else ''}: {', '.join(names[:8])}{f' and {len(names) - 8} more' if len(names) > 8 else ''}")
        if run:
            threading.Thread(target=self.run, args=(rec.id, sources), daemon=True, name=f"import-{rec.id[:8]}").start()
        return self.get(workspace, rec.id)

    def run(self, id: str, sources: list[SourcePrompt]) -> None:
        try:
            ai = self.ai
            if ai is None:
                raise AIError("no model is configured")
            with self._db() as db:
                ws = db.get(ImportRecord, id).workspace  # type: ignore[union-attr]
            with spend.scope(self.store.engine, ws, "onboarding"):
                proposal = extract(sources, ai, self.store.sopc, progress=lambda step, msg: self.event(id, step, msg))
            self._save(id, proposal, status="ready")
        except Exception as e:  # report every failure on the import itself
            log.exception("import %s failed", id)
            message = str(e) if isinstance(e, (AIError, SopcUnavailable, Invalid, ValueError)) else f"{type(e).__name__}: {e}"
            self.event(id, "failed", message[:2000])
            with self._db() as db:
                r = db.get(ImportRecord, id)
                if r:
                    r.status, r.error, r.updated_at = "failed", message[:4000], _now()
                    db.add(r)
                    db.commit()

    # --- sopc projects -----------------------------------------------------------------

    def import_project(self, workspace: str, files: dict[str, str], originals: dict[str, str] | None = None, author: str | None = None,
                       origin: str | None = None) -> dict:
        """An existing sopc project as an import, ready to review: what it holds, sopc's checks, and fidelity for each agent
        with an original prompt. `origin`: where the files came from (a repository and commit), for the log."""
        proposal = fidelity.check(project.proposal_of(files, self.store.sopc, originals), self.store.sopc)
        rec = ImportRecord(id=str(uuid.uuid4()), workspace=workspace_id(workspace), source="sopc", status="ready", repo=origin,
                           agents=[a.id for a in proposal.agents], proposal=proposal.model_dump(mode="json"), created_at=_now(), updated_at=_now(),
                           created_by=author)
        with self._db() as db:
            db.add(rec)
            db.commit()
        what = f"{len(proposal.agents)} agents, {len(proposal.instructions)} instructions, {len(proposal.procedures)} procedures, {len(proposal.files)} files"
        self.event(rec.id, "start", f"sopc project{f' from {origin}' if origin else ''}: {what}")
        self.event(rec.id, "fidelity", self._fidelity_line(proposal))
        return self.get(workspace, rec.id)

    def set_originals(self, workspace: str, id: str, originals: dict[str, str]) -> dict:
        """Add or replace agents' original prompts (an empty one removes it) and check fidelity again."""
        proposal = self._proposal(workspace, id)
        with self._db() as db:
            if self._load(db, workspace, id).status in DONE:
                raise Conflict("this import was already applied or discarded")
        unknown = sorted(set(originals) - {a.id for a in proposal.agents})
        if unknown:
            raise ValueError(f"no agent {', '.join(repr(a) for a in unknown)} in this import")
        merged = {**proposal.originals, **originals}
        proposal.originals = {k: v for k, v in merged.items() if v.strip()}
        proposal = fidelity.check(proposal, self.store.sopc)
        self._save(id, proposal)
        self.event(id, "fidelity", self._fidelity_line(proposal))
        return self.get(workspace, id)

    @staticmethod
    def _fidelity_line(p: Proposal) -> str:
        if not p.fidelity:
            return "No original prompts yet: add them to check that the project still says what each agent was told."
        return "Fidelity: " + ", ".join(f"{f.agent} {round(f.score * 100)}% kept, {f.rules_verbatim}/{f.rules} rules word for word" for f in p.fidelity)

    # --- agent imports: onboarding tokens --------------------------------------------

    def start_agent(self, workspace: str, server_url: str, mode: str = "hosted", author: str | None = None, ttl: timedelta = TOKEN_TTL) -> dict:
        """An onboarding token and the commands to start a coding agent. Repo mode needs the workspace's GitHub
        connection: its folder is read first (which checks the token still works), and the agent is told to open the
        onboarding pull request unless the folder already holds a project sopc builds. ValueError without a connection."""
        rec = ImportRecord(id=str(uuid.uuid4()), workspace=workspace_id(workspace), source="agent", status="waiting", mode=mode,
                           created_at=_now(), updated_at=_now(), created_by=author)
        if mode == "repo":
            if github.repo_connection(self.store, rec.workspace) is None:
                raise ValueError("repo mode needs a GitHub repository: connect one first")
            found = github.inspect_project(self.store, rec.workspace)
            rec.repo, rec.repo_branch, rec.repo_path, rec.repo_project = found["repo"], found["base_branch"], found["path"], found["state"]
        with self._db() as db:
            db.add(rec)
            db.commit()
        token = self.tokens.create(rec.workspace, [SCOPE], name="Import agents", expires_at=_now() + ttl, import_id=rec.id,
                                   created_by=author, prefix=TOKEN_PREFIX)["token"]
        self.event(rec.id, "start", "Waiting for Claude Code or Codex to connect", by="sopops")
        if rec.repo_project in ("missing", "invalid"):
            folder = f"{rec.repo_path}/" if rec.repo_path else "the repository root"
            self.event(rec.id, "repo", f"{rec.repo}: your agent will propose a pull request adding a sopc project at {folder} on "
                       f"{rec.repo_branch}. Nothing merges without you.", by="sopops")
        return {"import": self.get(workspace, rec.id), "token": token, "kickoff": kickoff.kickoff(workspace, server_url, token)}

    def session(self, rec: ImportRecord) -> dict:
        """What an onboarding token's agent is told about its session (`get_kickoff`). `repo_auth` says who opens the
        onboarding pull request: sopops (`app`, through the GitHub App) or the agent (`token`, with `gh`)."""
        token = self.token_of(rec) or {}
        conn = github.repo_connection(self.store, rec.workspace) if rec.mode == "repo" else None
        return {"workspace": rec.workspace, "import_id": rec.id, "mode": rec.mode, "repo": rec.repo, "repo_branch": rec.repo_branch,
                "repo_path": rec.repo_path, "repo_project": rec.repo_project, "repo_auth": conn.auth if conn else None, "pr_url": rec.pr_url,
                "scopes": token.get("scopes", []), "expires_at": token.get("expires_at")}

    def open_pull_request(self, rec: ImportRecord, description: str | None = None, by: str = "agent") -> dict:
        """sopops opens the onboarding pull request itself, from the pushed proposal (the coding agent doesn't need
        `gh`): its files at the connected folder on a new branch, replacing what the folder held when sopc couldn't build
        it. Then it's followed like a reported one. ValueError when there's nothing to open it with."""
        if rec.mode != "repo" or not rec.repo:
            raise ValueError("this import isn't in repo mode, so there's no onboarding pull request to open")
        if rec.pr_url and rec.pr_state == "open":
            return self.get(rec.workspace, rec.id)
        if rec.repo_project == "valid":
            raise ValueError("the repository already holds a sopc project sopc builds; there's no pull request to open")
        if rec.proposal is None:
            raise ValueError("push the proposal first (push_to_sopops); the pull request is made from it")
        proposal = Proposal.model_validate(rec.proposal)
        files, issues = (dict(proposal.files), []) if proposal.files else fidelity.files_of(ProposalContent(**proposal.model_dump(include=set(ProposalContent.model_fields))), self.store.sopc.export)
        if not files:
            raise ValueError("the proposal has nothing to write: " + "; ".join(i.message for i in issues)[:500])
        changes: dict[str, str | None] = dict(files)
        if rec.repo_project == "invalid":  # replace the project sopc couldn't build
            existing, _ = github.read_head(self.store, rec.workspace)
            changes |= {p: None for p in existing if p not in files}
        agents = ", ".join(a.name or a.id for a in proposal.agents) or "none"
        body = "\n\n".join(x for x in (description, f"Agents: {agents}.", f"Fidelity: {', '.join(f'{f.agent} {round(f.score * 100)}%' for f in proposal.fidelity)}."
                                        if proposal.fidelity else None, "Opened by sopops. When it merges, sopops pulls the folder into drafts.") if x)
        url = github.open_pull_request(self.store, rec.workspace, changes, "Add sopc project for sopops", body)
        pulls.record(self.store, rec, url, by=by)
        return self.get(rec.workspace, rec.id)

    def report_pull_request(self, rec: ImportRecord, url: str) -> dict:
        """The coding agent opened the onboarding pull request; sopops follows it. ValueError for a bad URL."""
        pulls.record(self.store, rec, url)
        return self.get(rec.workspace, rec.id)

    def authenticate(self, authorization: str | None, scope: str = SCOPE) -> ImportRecord:
        """The import an onboarding token belongs to. 401 for a missing, unknown, expired or revoked token (or one for a
        finished import); 403 without the scope."""
        t = self.tokens.check(None, authorization, scope)
        with self._db() as db:
            r = db.get(ImportRecord, t.import_id) if t.import_id else None
            if r is None:
                raise Forbidden(f"this token isn't for an import (it needs the {scope} scope from Import agents)")
            if r.status in DONE:
                raise Unauthorized("this import is finished, so its onboarding token was revoked; create a new one in sopops")
            return r

    def token_of(self, rec: ImportRecord) -> dict | None:
        t = self.tokens.for_import(rec.id)
        return token_out(t) if t else None

    def revoke(self, workspace: str, id: str) -> dict:
        with self._db() as db:
            r = self._load(db, workspace, id)
            self.tokens.revoke_import(db, r.id)
            r.updated_at = _now()
            db.add(r)
            db.commit()
        return self.get(workspace, id)

    def progress(self, rec: ImportRecord, step: str, message: str) -> dict:
        if rec.status == "waiting":
            self._set_status(rec.id, "running")
        return self.event(rec.id, step, message, by="agent")

    def push(self, rec: ImportRecord, content: ProposalContent, originals: dict[str, str], findings: list[Finding], notes: list[str]) -> dict:
        """Replace the import's proposal with this one (each push replaces the last), with fidelity checked here."""
        proposal = Proposal(**content.model_dump(), findings=findings, notes=notes, originals=originals)
        refresh_used_by(proposal)
        proposal = fidelity.check(proposal, self.store.sopc)
        with self._db() as db:
            r = db.get(ImportRecord, rec.id)
            assert r is not None
            r.proposal, r.status, r.pushes, r.agents = proposal.model_dump(mode="json"), "ready", r.pushes + 1, [a.name or a.id for a in proposal.agents]
            r.updated_at = _now()
            db.add(r)
            db.commit()
            pushes = r.pushes
        scores = ", ".join(f"{f.agent} {round(f.score * 100)}%" for f in proposal.fidelity)
        self.event(rec.id, "push", f"Proposal {'updated' if pushes > 1 else 'received'}: {len(proposal.agents)} agents, "
                   f"{len(proposal.instructions)} instructions, {len(proposal.procedures)} procedures" + (f"; fidelity {scores}" if scores else ""), by="agent")
        return {"id": rec.id, "pushes": pushes, "fidelity": [f.model_dump() for f in proposal.fidelity], "issues": [i.model_dump() for i in proposal.issues]}

    def check(self, content: ProposalContent, originals: dict[str, str]) -> dict:
        proposal = Proposal(**content.model_dump(), originals=originals)
        refresh_used_by(proposal)
        proposal = fidelity.check(proposal, self.store.sopc)
        return {"fidelity": [f.model_dump() for f in proposal.fidelity], "issues": [i.model_dump() for i in proposal.issues]}

    # --- review ------------------------------------------------------------------------

    def update(self, workspace: str, id: str, content: ProposalContent) -> dict:
        proposal = self._editable(workspace, id)
        merged = Proposal(**{**proposal.model_dump(), **content.model_dump()})
        refresh_used_by(merged)
        self._save(id, fidelity.check(merged, self.store.sopc))
        return self.get(workspace, id)

    def resolve(self, workspace: str, id: str, finding: str, choice: str) -> dict:
        proposal = resolve(self._editable(workspace, id), finding, choice)
        self._save(id, fidelity.check(proposal, self.store.sopc))
        return self.get(workspace, id)

    # --- the review, as summaries (see `summary`) ------------------------------------------------------------------

    def overlap(self, proposal: Proposal) -> dict:
        """`sopc overlap --json` on the originals, cached by their content (it's the same until they change)."""
        key = hashlib.sha256(json.dumps(proposal.originals, sort_keys=True).encode()).hexdigest()
        with self._overlap_lock:
            if key in self._overlaps:
                return self._overlaps[key]
        got = self.store.sopc.overlap(proposal.originals) if len(proposal.originals) > 1 else {}
        with self._overlap_lock:
            self._overlaps[key] = got
            while len(self._overlaps) > 32:
                self._overlaps.pop(next(iter(self._overlaps)))
        return got

    def review(self, workspace: str, id: str) -> dict:
        proposal = self._proposal(workspace, id)
        return summary.summary(proposal, self.overlap(proposal))

    def decide(self, workspace: str, id: str, group: str, choice: str) -> dict:
        proposal = self._editable(workspace, id)
        changed = summary.decide(proposal, self.overlap(proposal), group, choice)
        self._save(id, fidelity.check(changed, self.store.sopc) if changed.model_dump(exclude={"decisions"}) != proposal.model_dump(exclude={"decisions"}) else changed)
        return self.review(workspace, id)

    def edit_variable(self, workspace: str, id: str, name: str, new_name: str | None, values: dict[str, str] | None) -> dict:
        proposal = summary.edit_variable(self._editable(workspace, id), name, new_name, values)
        self._save(id, fidelity.check(proposal, self.store.sopc))
        return self.review(workspace, id)

    def discard(self, workspace: str, id: str) -> dict:
        with self._db() as db:
            r = self._load(db, workspace, id)
            r.status, r.updated_at = "discarded", _now()
            self.tokens.revoke_import(db, r.id)
            db.add(r)
            db.commit()
        self.event(id, "discarded", "Import discarded")
        return self.get(workspace, id)

    def apply(self, workspace: str, id: str, author: str | None = None, note: str | None = None, content: ProposalContent | None = None) -> dict:
        """Write the proposal's blocks, groups and agents as drafts. Items with the same id are replaced (as drafts)."""
        if content is not None:
            self.update(workspace, id, content)
        proposal = self._proposal(workspace, id)
        with self._db() as db:
            if self._load(db, workspace, id).status in DONE:
                raise Conflict("this import was already applied or discarded")
        if proposal.files:  # a sopc project: exactly its files
            if any(i.severity == "error" for i in proposal.issues):
                raise Conflict("sopc can't build this project; fix it and import it again")
            items = self.store.items(workspace)
            replaced = [f"{k}:{i.id}" for k, xs in (("agent", proposal.agents), ("instruction", proposal.instructions), ("procedure", proposal.procedures))
                        for i in xs if forms.find(items, k, i.id)]
            changes = dict(proposal.files)
        else:
            changes, replaced = self.changes(workspace, proposal)
        written = self.store.edit(workspace, changes, author, note or "Imported with sopops onboarding")
        with self._db() as db:
            r = self._load(db, workspace, id)
            r.status, r.updated_at = "applied", _now()
            self.tokens.revoke_import(db, r.id)
            db.add(r)
            db.commit()
        self.event(id, "applied", f"Applied as drafts: {len(written)} file(s). Review them in Unpublished changes, then publish.")
        return {"import": self.get(workspace, id), "changed": written, "replaced": replaced}

    def changes(self, workspace: str, content: ProposalContent) -> tuple[dict[str, str | None], list[str]]:
        """The file changes that write a proposal into the workspace's head, and the ids of items it replaces."""
        items = self.store.items(workspace)
        export = self.store.sopc.export
        changes: dict[str, str | None] = {}
        replaced: list[str] = []
        for kind, item_id, data in fidelity.item_data(content):
            current = forms.find(items, kind, item_id)
            other = forms.block_kind(items, item_id) if kind in forms.BLOCK_KINDS else None
            if other and other != kind:
                raise Conflict(f"'{item_id}' is already a {other} in this workspace; rename the {kind} in the proposal")
            issues = forms.check(kind, item_id, data)
            if issues:
                raise Invalid(issues)
            path, text, _ = forms.write(kind, item_id, data, current["file"] if current else None, export)
            changes[path] = text
            if current:
                replaced.append(f"{kind}:{item_id}")
                if current["file"] != path:
                    changes[current["file"]] = None
        config = forms.config_of(items)
        for g in content.groups:
            if forms.block_kind(items, g.id) not in (None, "group"):
                raise Conflict(f"'{g.id}' is already a block in this workspace; rename the group in the proposal")
            if g.id in config["groups"]:
                replaced.append(f"group:{g.id}")
            config["groups"][g.id] = list(g.blocks)
        config["variables"] = {**config["variables"], **content.settings.variables}
        if not items["agents"] and not items["instructions"] and not items["procedures"]:
            config["procedures_heading"] = content.settings.procedures_heading or forms.DEFAULT_HEADING
        changes[forms.CONFIG] = forms.write("config", "config", config, None, export)[1]
        return changes, replaced

    # --- internals ------------------------------------------------------------------------

    def _proposal(self, workspace: str, id: str) -> Proposal:
        with self._db() as db:
            r = self._load(db, workspace, id)
            if r.proposal is None:
                raise Conflict("this import has no proposal yet")
            return Proposal.model_validate(r.proposal)

    def _editable(self, workspace: str, id: str) -> Proposal:
        proposal = self._proposal(workspace, id)
        if proposal.files:
            raise Conflict("an imported sopc project is applied as its files: change the project and import it again")
        return proposal

    def _save(self, id: str, proposal: Proposal, status: str | None = None) -> None:
        with self._db() as db:
            r = db.get(ImportRecord, id)
            if r is None:
                return
            r.proposal = proposal.model_dump(mode="json")
            r.agents = [a.name or a.id for a in proposal.agents]
            if status:
                r.status = status
            r.updated_at = _now()
            db.add(r)
            db.commit()

    def _set_status(self, id: str, status: str) -> None:
        with self._db() as db:
            r = db.get(ImportRecord, id)
            if r:
                r.status, r.updated_at = status, _now()
                db.add(r)
                db.commit()

    def event(self, id: str, step: str, message: str, by: str = "sopops") -> dict:
        with self._db() as db:
            e = ImportEvent(import_id=id, at=_now(), step=step[:64], message=message[:4000], by=by)
            db.add(e)
            db.commit()
            db.refresh(e)
            return event_out(e)


def token_out(t: Token, rec: ImportRecord | None = None) -> dict:
    o = token_info(t)
    active = o["active"] and (rec is None or rec.status not in DONE)
    return {"prefix": o["prefix"], "scopes": o["scopes"], "expires_at": o["expires_at"], "revoked_at": o["revoked_at"], "active": active}


def out(r: ImportRecord, token: Token | None = None, proposal: bool = True) -> dict:
    token = token_out(token, r) if token is not None else None
    d = {
        "id": r.id, "workspace": r.workspace, "source": r.source, "status": r.status, "mode": r.mode, "repo": r.repo,
        "repo_branch": r.repo_branch, "repo_path": r.repo_path, "repo_project": r.repo_project,
        "pr_url": r.pr_url, "pr_state": r.pr_state, "pr_merged_at": _utc(r.pr_merged_at) if r.pr_merged_at else None, "agents": r.agents, "error": r.error, "pushes": r.pushes, "token": token,
        "created_at": _utc(r.created_at), "updated_at": _utc(r.updated_at), "created_by": r.created_by,
    }
    if proposal:
        d["proposal"] = r.proposal
    return d


def event_out(e: ImportEvent) -> dict:
    return {"id": e.id, "at": _utc(e.at), "step": e.step, "message": e.message, "by": e.by}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
