"""Imports: running an extraction here, onboarding tokens for coding agents, review, and applying a proposal as drafts.

Two ways in, one way out:

- **LLM import** (`source: llm`): prompts pasted, uploaded or read from ElevenLabs/Vapi are extracted here, in a
  background thread, with the configured model (`sopops.llm`).
- **Agent import** (`source: agent`): the user starts Claude Code or Codex in their repo with the `sopops-mcp` server
  and an onboarding token. The agent reports progress and pushes a proposal; each push replaces the last one.

Either way the user reviews the proposal (edits, drift decisions, fidelity) and applies it: its blocks, groups and
agents are written as drafts, for the usual Unpublished changes review and publish.

Onboarding tokens are `tokens` rows tied to the import (see `sopops.tokens`): single-workspace, scoped to `onboarding:write` (push a proposal, post progress: nothing else),
stored as a sha256 hash, shown once, and valid until they expire (2 hours), are revoked, or the import is applied or
discarded.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlmodel import Session, select

from .. import forms
from ..llm import Completer, LLMError, from_env
from ..sopc import Invalid, SopcUnavailable
from ..store import Conflict, NotFound, Store
from . import fidelity, kickoff
from .models import Finding, Proposal, ProposalContent, SourcePrompt
from .pipeline import extract
from ..models import Token
from ..tokens import Forbidden, Tokens, Unauthorized
from ..tokens import out as token_info
from .records import ImportEvent, ImportRecord
from .review import refresh_used_by, resolve

log = logging.getLogger("sopops.onboarding")

SCOPE = "onboarding:write"
TOKEN_PREFIX = "sopops_onb_"
TOKEN_TTL = timedelta(hours=2)
DONE = ("applied", "discarded")


def llm_from_env() -> Completer | None:
    """The configured model, the rules completer (`SOPOPS_MODEL_REASONING=rules`, for trying it out), or None."""
    return from_env()


class Onboarding:
    def __init__(self, store: Store, llm: Completer | None | Callable[[], Completer | None] = llm_from_env):
        self.store = store
        self._llm = llm
        self._fail_interrupted()

    @property
    def llm(self) -> Completer | None:
        return self._llm() if callable(self._llm) and not hasattr(self._llm, "complete") else self._llm  # type: ignore[return-value]

    @llm.setter
    def llm(self, value: Completer | None) -> None:
        self._llm = value

    @property
    def tokens(self) -> Tokens:
        return self.store.tokens

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    def _fail_interrupted(self) -> None:
        from sqlalchemy import inspect

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
        with self._db() as db:
            r = self._load(db, workspace, id)
            return out(r, self.tokens.for_import(r.id, db))

    def events(self, workspace: str, id: str, after: int = 0) -> list[dict]:
        with self._db() as db:
            self._load(db, workspace, id)
            rows = db.exec(select(ImportEvent).where(ImportEvent.import_id == id, ImportEvent.id > after).order_by(ImportEvent.id))
            return [event_out(e) for e in rows]

    def _load(self, db: Session, workspace: str, id: str) -> ImportRecord:
        r = db.get(ImportRecord, id)
        if r is None or r.workspace != workspace:
            raise NotFound(f"import '{id}' not found")
        return r

    # --- LLM imports ----------------------------------------------------------

    def start(self, workspace: str, sources: list[SourcePrompt], author: str | None = None, run: bool = True) -> dict:
        """Start extracting in a background thread. Raises LLMError if no model is configured."""
        if self.llm is None:
            raise LLMError("No model is configured for extraction. Set SOPOPS_MODEL_REASONING (and optionally SOPOPS_MODEL_FAST) "
                           "to a LiteLLM model name and its provider key, or use Claude Code / Codex instead.")
        if not sources:
            raise ValueError("add at least one prompt")
        names = [s.name for s in sources]
        rec = ImportRecord(id=str(uuid.uuid4()), workspace=_ws(workspace), source="llm", status="running", agents=names,
                           created_at=_now(), updated_at=_now(), created_by=author)
        with self._db() as db:
            db.add(rec)
            db.commit()
        self.event(rec.id, "start", f"Importing {len(sources)} agent{'s' if len(sources) != 1 else ''}: {', '.join(names)}")
        if run:
            threading.Thread(target=self.run, args=(rec.id, sources), daemon=True, name=f"import-{rec.id[:8]}").start()
        return self.get(workspace, rec.id)

    def run(self, id: str, sources: list[SourcePrompt]) -> None:
        try:
            llm = self.llm
            if llm is None:
                raise LLMError("no model is configured")
            proposal = extract(sources, llm, self.store.sopc, progress=lambda step, msg: self.event(id, step, msg))
            self._save(id, proposal, status="ready")
        except Exception as e:  # report every failure on the import itself
            log.exception("import %s failed", id)
            message = str(e) if isinstance(e, (LLMError, SopcUnavailable, Invalid, ValueError)) else f"{type(e).__name__}: {e}"
            self.event(id, "failed", message[:2000])
            with self._db() as db:
                r = db.get(ImportRecord, id)
                if r:
                    r.status, r.error, r.updated_at = "failed", message[:4000], _now()
                    db.add(r)
                    db.commit()

    # --- agent imports: onboarding tokens --------------------------------------------

    def start_agent(self, workspace: str, server_url: str, mode: str = "hosted", repo: str | None = None, author: str | None = None,
                    ttl: timedelta = TOKEN_TTL) -> dict:
        rec = ImportRecord(
            id=str(uuid.uuid4()), workspace=_ws(workspace), source="agent", status="waiting", mode=mode, repo=(repo or "").strip() or None,
            created_at=_now(), updated_at=_now(), created_by=author,
        )
        with self._db() as db:
            db.add(rec)
            db.commit()
        token = self.tokens.create(rec.workspace, [SCOPE], name="Import agents", expires_at=_now() + ttl, import_id=rec.id,
                                   created_by=author, prefix=TOKEN_PREFIX)["token"]
        self.event(rec.id, "start", "Waiting for Claude Code or Codex to connect", by="sopops")
        return {"import": self.get(workspace, rec.id), "token": token, "kickoff": kickoff.kickoff(workspace, server_url, token, mode, rec.repo)}

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
        proposal = self._proposal(workspace, id)
        merged = Proposal(**{**proposal.model_dump(), **content.model_dump()})
        refresh_used_by(merged)
        self._save(id, fidelity.check(merged, self.store.sopc))
        return self.get(workspace, id)

    def resolve(self, workspace: str, id: str, finding: str, choice: str) -> dict:
        proposal = resolve(self._proposal(workspace, id), finding, choice)
        self._save(id, fidelity.check(proposal, self.store.sopc))
        return self.get(workspace, id)

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
        "agents": r.agents, "error": r.error, "pushes": r.pushes, "token": token,
        "created_at": _utc(r.created_at), "updated_at": _utc(r.updated_at), "created_by": r.created_by,
    }
    if proposal:
        d["proposal"] = r.proposal
    return d


def event_out(e: ImportEvent) -> dict:
    return {"id": e.id, "at": _utc(e.at), "step": e.step, "message": e.message, "by": e.by}


def _ws(workspace: str) -> str:
    from ..store import _ws as check

    return check(workspace)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
