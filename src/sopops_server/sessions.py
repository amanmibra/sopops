"""Sessions: ingest (an idempotent upsert on workspace + platform + external id) and listing. Tokens are in `tokens`.

A session is one conversation an agent had: a voice call or a text chat. Every write publishes a session event
(see `events`), and ingest hands the session to the pipeline (see `jobs`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import event as sa_event
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, and_, or_, select, text

from . import configs, lineage, tags
from .auth import actor
from .events import CHANNEL, Broker
from .orm import (
    Finding,
    Pattern,
    ProcedureCheck,
    PromptText,
    Release,
    RuleResult,
    SessionRecord,
    Workspace,
)
from .pipeline.normalize import normalize_tool_calls, normalize_turns, to_utc
from .pipeline.types import IDLE_INCOMPLETE, SENDER_INCOMPLETE, SessionRef
from .runtime.service import served_release
from .schemas import SessionEventIn, SessionIn, SessionOpen

if TYPE_CHECKING:
    from .store import Store

from .errors import NotFound, workspace_id
from .tokens import Unauthorized  # noqa: F401  (re-exported: the app maps it to 401)

IDLE = timedelta(minutes=int(os.environ.get("SOPOPS_SESSION_IDLE_MINUTES", "30")))  # an open session with no events this long is closed


class BadCursor(ValueError):
    pass


@dataclass
class Ingested:
    id: str
    created: bool
    updated: bool
    status: str
    warnings: list[str]
    ref: SessionRef

    @property
    def changed(self) -> bool:
        return self.created or self.updated


class Sessions:
    def __init__(self, store: Store, broker: Broker | None = None):
        self.store = store
        self.broker = broker or Broker()
        self._agents_by_release: dict[tuple[str, int], dict[str, str]] = {}

    @property
    def postgres(self) -> bool:
        return self.store.engine.dialect.name == "postgresql"

    # --- ingest ----------------------------------------------------------------

    def ingest(self, workspace: str, payload: SessionIn) -> Ingested:
        """Create the session, or update it if its content changed. Re-sending identical content changes nothing."""
        ws = workspace_id(workspace)
        for attempt in range(2):
            try:
                return self._ingest(ws, payload)
            except IntegrityError:
                if attempt:  # lost a race for the same key twice: shouldn't happen
                    raise
        raise AssertionError("unreachable")

    def _ingest(self, ws: str, p: SessionIn) -> Ingested:
        body = p.model_dump(mode="json")
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        now = _now()
        warnings: list[str] = []
        with self._db() as db, db.begin():
            self._ensure_workspace(db, ws)
            q = select(SessionRecord).where(
                SessionRecord.workspace_id == ws, SessionRecord.platform == p.platform, SessionRecord.external_id == p.external_id
            )
            row = db.exec(q.with_for_update() if self.postgres else q).first()
            if row is not None and row.payload_hash == digest:
                return Ingested(row.id, False, False, row.status, [], SessionRef(workspace_id=ws, session_id=row.id, revision=digest))

            release = p.release
            if release is None and p.platform in ("elevenlabs", "vapi"):  # served by a call-start webhook
                release = served_release(db, ws, p.platform, _call_keys(p))
            if release is not None and db.get(Release, (ws, release)) is None:
                warnings.append(f"Release {release} doesn't exist in workspace '{ws}'; the session is kept without it.")
                release = None
            against = release or db.exec(select(Workspace.current_release).where(Workspace.workspace == ws)).first()
            agent_id = self._match_agent(db, ws, p.agent, p.platform, against) if p.agent and against else None
            if p.agent and against and agent_id is None:
                warnings.append(f"No agent '{p.agent}' in release {against}; the session keeps the name as sent.")
            test, config, chash, more = self._config(db, ws, p.test, p.config, p.metadata, now)
            warnings += more
            # a turn can't come before the session started (a helper may note the start a moment late)
            started = min((t for t in (to_utc(p.started_at), _first_time(p)) if t), default=now)
            fields = dict(
                agent_id=agent_id,
                agent_ref=p.agent,
                channel=p.channel,
                release_id=release,
                version_ref=p.version_ref,
                test=test,
                config=config,
                config_hash=chash,
                started_at=started,
                ended_at=to_utc(p.ended_at),
                status="incomplete" if p.partial else "received",
                status_reason=SENDER_INCOMPLETE if p.partial else None,
                turns=body["turns"],
                tool_calls=body["tool_calls"],
                meta=body["metadata"],
                payload_hash=digest,
                updated_at=now,
                processed_at=None,
                result=None,
            )
            created = row is None
            if created:
                row = SessionRecord(id=str(uuid.uuid4()), workspace_id=ws, platform=p.platform, external_id=p.external_id, created_at=now, **fields)
            else:
                for k, v in fields.items():
                    setattr(row, k, v)
            db.add(row)
            db.flush()
            warnings += self._tag(db, ws, row, created, p.tags, p.metadata)
            self._emit(db, {"type": "created" if created else "updated", "workspace": ws, "id": row.id, "status": row.status})
            return Ingested(row.id, created, not created, row.status, warnings, SessionRef(workspace_id=ws, session_id=row.id, revision=digest))

    # --- streaming (optional): open, append events, close ----------------------------------------------------------

    def open(self, workspace: str, p: SessionOpen) -> Ingested:
        """Start a session that streams in. Idempotent: an existing session (open or already posted) is returned as is."""
        ws = workspace_id(workspace)
        now = _now()
        with self._db() as db, db.begin():
            self._ensure_workspace(db, ws)
            row = self._row(db, ws, p.platform, p.external_id)
            if row is not None:
                return Ingested(row.id, False, False, row.status, [], SessionRef(workspace_id=ws, session_id=row.id, revision=row.payload_hash))
            warnings: list[str] = []
            release = p.release
            if release is not None and db.get(Release, (ws, release)) is None:
                warnings.append(f"Release {release} doesn't exist in workspace '{ws}'; the session is kept without it.")
                release = None
            against = release or db.exec(select(Workspace.current_release).where(Workspace.workspace == ws)).first()
            agent_id = self._match_agent(db, ws, p.agent, p.platform, against) if p.agent and against else None
            test, config, chash, more = self._config(db, ws, p.test, p.config, p.metadata, now)
            warnings += more
            row = SessionRecord(
                id=str(uuid.uuid4()), workspace_id=ws, platform=p.platform, external_id=p.external_id, created_at=now, updated_at=now,
                agent_id=agent_id, agent_ref=p.agent, channel=p.channel, release_id=release, version_ref=p.version_ref,
                test=test, config=config, config_hash=chash, started_at=to_utc(p.started_at) or now, ended_at=None, status="open", status_reason=None, turns=[], tool_calls=[],
                meta=p.model_dump(mode="json")["metadata"], payload_hash=_digest({"open": p.external_id}), processed_at=None, result=None,
            )
            db.add(row)
            db.flush()
            warnings += self._tag(db, ws, row, True, p.tags, p.metadata)
            self._emit(db, {"type": "created", "workspace": ws, "id": row.id, "status": row.status})
            return Ingested(row.id, True, False, row.status, warnings, SessionRef(workspace_id=ws, session_id=row.id, revision=row.payload_hash))

    def append(self, workspace: str, platform: str, external_id: str, events: list[SessionEventIn]) -> tuple[SessionRef, int, int]:
        """Add events to an open session, in `seq` order; a `seq` already stored is ignored, and so is anything sent
        after the session closed (its final post wins). Returns the session and how many were accepted and ignored."""
        ws = workspace_id(workspace)
        with self._db() as db, db.begin():
            row = self._row(db, ws, platform, external_id)
            if row is None:
                raise NotFound(f"no session '{platform}/{external_id}': open it first")
            ref = SessionRef(workspace_id=ws, session_id=row.id, revision=row.payload_hash)
            if row.status != "open":
                return ref, 0, len(events)
            seen = {x.get("seq") for x in (*row.turns, *row.tool_calls)}
            turns, calls, accepted = list(row.turns), list(row.tool_calls), 0
            for e in events:
                item = e.turn if e.type == "turn" else e.tool_call
                if e.seq in seen or item is None:
                    continue
                seen.add(e.seq)
                (turns if e.type == "turn" else calls).append({**item.model_dump(mode="json", exclude_none=True), "seq": e.seq})
                accepted += 1
            if accepted:
                row.turns = sorted(turns, key=lambda x: x["seq"])
                row.tool_calls = sorted(calls, key=lambda x: x["seq"])
                row.updated_at = _now()
                db.add(row)
            return ref, accepted, len(events) - accepted

    def replace_streamed(self, workspace: str, platform: str, external_id: str, turns: list[dict], tool_calls: list[dict]) -> tuple[SessionRef, bool]:
        """Set an open session's turns and tool calls to the whole conversation so far, for a platform that sends it
        whole on every update (Vapi's `conversation-update`). Each item gets its position as `seq`. A session that
        isn't open is left as is. Returns the session and whether anything changed."""
        ws = workspace_id(workspace)
        with self._db() as db, db.begin():
            row = self._row(db, ws, platform, external_id)
            if row is None:
                raise NotFound(f"no session '{platform}/{external_id}': open it first")
            ref = SessionRef(workspace_id=ws, session_id=row.id, revision=row.payload_hash)
            if row.status != "open":
                return ref, False
            n = len(turns)
            new_turns = [{**t, "seq": i} for i, t in enumerate(turns)]
            new_calls = [{**c, "seq": n + i} for i, c in enumerate(tool_calls)]
            if new_turns == row.turns and new_calls == row.tool_calls:
                return ref, False
            row.turns, row.tool_calls, row.updated_at = new_turns, new_calls, _now()
            db.add(row)
            return ref, True

    def close(self, workspace: str, platform: str, external_id: str, ended_at: datetime | None = None, partial: bool = False,
              reason: str | None = None, test: str | None = None, config: dict[str, Any] | None = None, tags: list[str] | None = None) -> Ingested:
        """Close an open session so it's checked, with what streamed in. A session that isn't open is left as is.
        `test` and `config` (settings learned during the call) are merged over what was sent at open; `tags` are added."""
        ws = workspace_id(workspace)
        with self._db() as db, db.begin():
            row = self._row(db, ws, platform, external_id)
            if row is None:
                raise NotFound(f"no session '{platform}/{external_id}'")
            if row.status != "open":
                return Ingested(row.id, False, False, row.status, [], SessionRef(workspace_id=ws, session_id=row.id, revision=row.payload_hash))
            if test or config:
                known = dict(row.config or {})
                kept = known.pop("prompt", None)  # already stored as its hash
                row.test, merged, _, _ = self._config(db, ws, test or row.test, configs.merge(known, config), {}, _now())
                merged = dict(merged or {})
                if kept and "prompt" not in merged:
                    merged["prompt"] = kept
                row.config, row.config_hash = merged or None, configs.config_hash(merged)
            warnings = self._tag(db, ws, row, False, tags or [], None)
            row.status = "incomplete" if partial else "received"
            row.status_reason = (reason or SENDER_INCOMPLETE) if partial else None
            row.ended_at = to_utc(ended_at) or row.ended_at or _now()
            row.payload_hash = _digest({"turns": row.turns, "tool_calls": row.tool_calls, "metadata": row.meta, "closed": row.ended_at.isoformat()})
            row.updated_at = _now()
            db.add(row)
            db.flush()
            self._emit(db, {"type": "updated", "workspace": ws, "id": row.id, "status": row.status})
            return Ingested(row.id, False, True, row.status, warnings, SessionRef(workspace_id=ws, session_id=row.id, revision=row.payload_hash))

    def close_idle(self, workspace: str | None = None, idle: timedelta = IDLE) -> list[SessionRef]:
        """Close open sessions with no events for `idle` as incomplete (the sender never posted or closed them)."""
        cutoff = _now() - idle
        with self._db() as db:
            q = select(SessionRecord.workspace_id, SessionRecord.platform, SessionRecord.external_id).where(SessionRecord.status == "open", SessionRecord.updated_at < cutoff)
            if workspace is not None:
                q = q.where(SessionRecord.workspace_id == workspace_id(workspace))
            stale = list(db.exec(q))
        return [self.close(ws, platform, ext, partial=True, reason=IDLE_INCOMPLETE).ref for ws, platform, ext in stale]

    def _row(self, db: Session, ws: str, platform: str, external_id: str) -> SessionRecord | None:
        q = select(SessionRecord).where(SessionRecord.workspace_id == ws, SessionRecord.platform == platform, SessionRecord.external_id == external_id)
        return db.exec(q.with_for_update() if self.postgres else q).first()

    def finish(self, ref: SessionRef, turns: list[dict], tool_calls: list[dict], status: str, reason: str | None) -> bool:
        """Store the pipeline's result, unless the session was re-sent since the run was queued. True if stored."""
        with self._db() as db, db.begin():
            q = select(SessionRecord).where(SessionRecord.workspace_id == ref.workspace_id, SessionRecord.id == ref.session_id)
            row = db.exec(q.with_for_update() if self.postgres else q).first()
            if row is None or row.payload_hash != ref.revision:
                return False
            now = _now()
            row.turns, row.tool_calls, row.status, row.status_reason, row.result = turns, tool_calls, status, reason, None
            row.processed_at = row.updated_at = now
            db.add(row)
            db.flush()
            self._emit(db, {"type": "updated", "workspace": row.workspace_id, "id": row.id, "status": status})
            return True

    # --- reading ---------------------------------------------------------------

    def load(self, workspace: str, id: str) -> SessionRecord:
        with self._db() as db:
            row = db.exec(select(SessionRecord).where(SessionRecord.workspace_id == workspace_id(workspace), SessionRecord.id == id)).first()
        if row is None:
            raise NotFound(f"session '{id}' not found")
        return row

    def get(self, workspace: str, id: str) -> dict:
        row = self.load(workspace, id)
        # as normalizing stores them: a session is stored as sent and normalized a moment later (by a run that a re-send
        # can overtake), and normalizing what's already normalized changes nothing
        turns, calls = normalize_turns(row.turns, row.started_at), normalize_tool_calls(row.tool_calls, row.started_at)
        with self._db() as db:
            tagged = tags.for_sessions(db, [row.id])[row.id]
        return {**_summary(row), "tags": tagged, "turns": turns, "tool_calls": calls, "metadata": row.meta, "checks": self.store.checks.for_session(row),
                **self.store.usage.session(row.workspace_id, row.id)}

    def list(
        self,
        workspace: str,
        *,
        agent: str | None = None,
        channel: str | None = None,
        status: str | None = None,
        result: str | None = None,
        platform: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        cursor: str | None = None,
        limit: int = 50,
        release: int | None = None,
        procedure: str | None = None,
        procedure_result: str | None = None,
        rule: str | None = None,
        rule_status: list[str] | None = None,
        pattern: str | None = None,
        theme: str | None = None,
        finding: bool | None = None,
        eligible: bool | None = None,
        suppressed: bool | None = None,
        tag: list[str] | None = None,
    ) -> dict:
        """Newest first (by start time). `agent` matches the sop agent or the agent as sent. The drill-down filters
        (procedure, its result, rule and status, pattern, theme, any finding) each narrow to sessions with such a
        check, rule result or finding; a rule pattern includes its sub-patterns, a theme every pattern in it. `suppressed`
        false leaves suppressed findings (and their rule results) out of those filters, true matches only them."""
        S = SessionRecord
        found = select(Finding.session_id).where(Finding.workspace_id == workspace_id(workspace))
        if suppressed is not None:
            found = found.where(Finding.suppressed.is_(suppressed))
        q = select(S).where(S.workspace_id == workspace_id(workspace))
        if release is not None:
            q = q.where(S.release_id == release)
        if procedure or procedure_result:
            pc = select(ProcedureCheck.session_id)
            if procedure:
                pc = pc.where(ProcedureCheck.procedure_id == procedure)
            if procedure_result:
                pc = pc.where(ProcedureCheck.result == procedure_result)
            q = q.where(S.id.in_(pc))
        if rule or rule_status:  # rule_status alone: any rule with those statuses
            rr = select(RuleResult.session_id).where(RuleResult.workspace_id == workspace_id(workspace))
            if rule:  # any wording of the rule (sopops_server.lineage)
                with self._db() as db:
                    rr = rr.where(RuleResult.rule_id.in_(lineage.expand(db, workspace_id(workspace), rule, procedure)[1]))
            if procedure:
                rr = rr.where(RuleResult.procedure_id == procedure)
            if rule_status:
                rr = rr.where(RuleResult.status.in_(rule_status))
            if suppressed is not None:
                hidden = RuleResult.id.in_(select(Finding.rule_result_id).where(Finding.suppressed.is_(True)))
                rr = rr.where(hidden if suppressed else ~hidden)
            q = q.where(S.id.in_(rr))
        if pattern or theme:
            pats = select(Pattern.id).where(Pattern.workspace_id == workspace_id(workspace))
            pats = pats.where(or_(Pattern.id == pattern, Pattern.parent_id == pattern)) if pattern else pats.where(Pattern.theme_id == theme)
            q = q.where(S.id.in_(found.where(Finding.pattern_id.in_(pats))))
        if eligible is not None:  # Insights' eligible sessions: checked (or incomplete) with a result
            ok = and_(S.status.in_(("checked", "incomplete")), S.result.is_not(None))
            q = q.where(ok if eligible else ~ok)
        if finding is not None:
            has = S.id.in_(found)
            q = q.where(has if finding else ~has)
        if agent:
            q = q.where(or_(S.agent_id == agent, S.agent_ref == agent))
        if tag:
            q = q.where(*tags.has_all(workspace_id(workspace), tags.clean(tag, strict=False), S))
        if channel:
            q = q.where(S.channel == channel)
        if status:
            q = q.where(S.status == status)
        if result:
            q = q.where(S.result == result)
        if platform:
            q = q.where(S.platform == platform)
        if since:
            q = q.where(S.started_at >= to_utc(since))
        if until:
            q = q.where(S.started_at < to_utc(until))
        if cursor:
            at, id = _decode_cursor(cursor)
            q = q.where(or_(S.started_at < at, and_(S.started_at == at, S.id < id)))
        q = q.order_by(S.started_at.desc(), S.id.desc()).limit(limit + 1)
        with self._db() as db:
            rows = list(db.exec(q))
            more = len(rows) > limit
            rows = rows[:limit]
            tagged = tags.for_sessions(db, [r.id for r in rows])
            items = [{**_summary(r), "tags": tagged[r.id]} for r in rows]
            if procedure and items:  # that procedure's check on each session, one dot per rule
                ids = [r.id for r in rows]
                checks = {pc.session_id: pc for pc in db.exec(select(ProcedureCheck).where(ProcedureCheck.session_id.in_(ids), ProcedureCheck.procedure_id == procedure))}
                dots: dict[str, list[dict]] = {}
                for rr in db.exec(select(RuleResult).where(RuleResult.session_id.in_(ids), RuleResult.procedure_id == procedure)):
                    dots.setdefault(rr.session_id, []).append({"rule_id": rr.rule_id, "kind": rr.kind, "index": rr.index, "status": rr.status})
                order = {"step": 0, "never": 1, "warning": 2}
                for it in items:
                    pc = checks.get(it["id"])
                    if pc is not None:
                        it["procedure_check"] = {"procedure_id": procedure, "result": pc.result, "goal_reached": pc.goal_reached,
                                                 "rules": sorted(dots.get(it["id"], []), key=lambda d: (order.get(d["kind"], 3), d["index"]))}
        return {"items": items, "next_cursor": _encode_cursor(rows[-1]) if more else None}

    # --- internals -------------------------------------------------------------

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    def _config(self, db: Session, ws: str, test: str | None, config: dict[str, Any] | None, metadata: dict[str, Any] | None,
                now: datetime) -> tuple[str | None, dict[str, Any] | None, str | None, list[str]]:
        """(test, config as stored, its hash, warnings): `sopops_*` metadata over `config` (see `sopops_server.configs`); a
        reported prompt is kept once in `prompt_texts`."""
        meta_test, meta_config = configs.from_metadata(metadata)
        merged, warnings = configs.fit(configs.merge(config, meta_config))
        stored, chash, prompt = configs.stored(merged)
        if prompt is not None and db.get(PromptText, (ws, stored["prompt"])) is None:
            dialect = postgresql if self.postgres else sqlite
            db.execute(dialect.insert(PromptText.__table__).values(workspace_id=ws, hash=stored["prompt"], text=prompt, created_at=now).on_conflict_do_nothing())
        test = (meta_test or test or "").strip()[: configs.MAX_TEST] or None
        return test, stored or None, chash, warnings

    def _tag(self, db: Session, ws: str, row: SessionRecord, created: bool, sent: list[str], metadata: dict[str, Any] | None) -> list[str]:
        """Give the session the tags its sender passed and, when it's new, its agent's tags as they are now: a copy, so
        changing the agent's tags later leaves the session as it was. Returns warnings for tags it couldn't use."""
        kept = tags.clean(sent, strict=False)
        warnings = [f"Tag '{t}' isn't valid (letters, digits and - _ . : /, up to 64 characters); it was left out."
                    for t in sent if tags.normalize(t) not in kept]
        add = [*(tags.for_agent(db, ws, row.agent_id) if created else []), *kept, *tags.from_metadata(metadata)]
        if add:
            tags.add_to_session(db, ws, row.id, add)
        return warnings

    def agent_tags(self, workspace: str) -> dict:
        ws = workspace_id(workspace)
        with self._db() as db:
            return {"agents": tags.by_agent(db, ws), "used": tags.used(db, ws)}

    def set_agent_tags(self, workspace: str, agent: str, new: list[str]) -> list[str]:
        """Replace an agent's tags. Sessions already in keep the tags they came in with."""
        ws = workspace_id(workspace)
        cleaned = tags.clean(new)
        with self._db() as db, db.begin():
            self._ensure_workspace(db, ws)
            tags.set_agent(db, ws, agent[:256], cleaned)
        return cleaned

    def set_tags(self, workspace: str, id: str, new: list[str]) -> list[str]:
        """Replace a session's tags."""
        row = self.load(workspace, id)
        cleaned = tags.clean(new)
        with self._db() as db, db.begin():
            tags.set_session(db, row.workspace_id, row.id, cleaned)
        return cleaned

    def _ensure_workspace(self, db: Session, ws: str) -> None:
        dialect = postgresql if self.postgres else sqlite
        if db.get(Workspace, ws) is None:
            db.execute(dialect.insert(Workspace.__table__).values(workspace=ws, org_id=actor.workspace_org(db)).on_conflict_do_nothing())

    def notify(self, event: dict) -> None:
        """Publish an event that isn't part of a session write (a repository sync, say) to the workspace's stream, in
        every process on Postgres."""
        if self.postgres:
            with self._db() as db, db.begin():
                db.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": json.dumps(event, default=str)})
        else:
            self.broker.publish(event)

    def _emit(self, db: Session, event: dict) -> None:
        """Postgres: NOTIFY in this transaction (delivered on commit, to every process). SQLite: publish after commit."""
        if self.postgres:
            db.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": json.dumps(event)})
        else:
            sa_event.listen(db, "after_commit", lambda _s: self.broker.publish(event), once=True)

    def _match_agent(self, db: Session, ws: str, agent: str, platform: str, number: int) -> str | None:
        """The sop agent `agent` names in the release: its sop id, its platform ref (`livekit:tonys-pizza`),
        or its platform id on this session's platform."""
        refs = self._agents_by_release.get((ws, number))
        if refs is None:  # releases are immutable, so this is cached for good
            rel = db.get(Release, (ws, number))
            refs = {a: b["platform_ref"] for a, b in (rel.build["agents"] if rel else {}).items()}
            self._agents_by_release[(ws, number)] = refs
        if agent in refs:
            return agent
        for a, ref in refs.items():
            if agent == ref or f"{platform}:{agent}" == ref:
                return a
        return None


def _first_time(p: SessionIn) -> datetime | None:
    times = [to_utc(t.at) for t in p.turns if t.at is not None]
    return min(times) if times else None


def _summary(r: SessionRecord) -> dict[str, Any]:
    started, ended = _utc(r.started_at), _utc(r.ended_at) if r.ended_at else None
    return {
        "id": r.id,
        "workspace": r.workspace_id,
        "agent_id": r.agent_id,
        "agent_ref": r.agent_ref,
        "channel": r.channel,
        "platform": r.platform,
        "external_id": r.external_id,
        "release_id": r.release_id,
        "version_ref": r.version_ref,
        "test": r.test,
        "config": r.config,
        "config_hash": r.config_hash,
        "started_at": started,
        "ended_at": ended,
        "duration_s": (ended - started).total_seconds() if ended else None,
        "status": r.status,
        "status_reason": r.status_reason,
        "result": r.result,
        "turn_count": len(r.turns),
        "tool_call_count": len(r.tool_calls),
        "created_at": _utc(r.created_at),
        "updated_at": _utc(r.updated_at),
        "processed_at": _utc(r.processed_at) if r.processed_at else None,
    }


def _encode_cursor(r: SessionRecord) -> str:
    raw = json.dumps([_utc(r.started_at).isoformat(), r.id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        at, id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        return to_utc(at), str(id)
    except Exception as e:
        raise BadCursor("invalid cursor") from e



def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime) -> datetime:
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _call_keys(p: SessionIn) -> list:
    """Ids a call-start webhook may have keyed this call by: the session's own id, or a phone call's sid."""
    meta = p.metadata or {}
    phone = meta.get("phone_call") if isinstance(meta.get("phone_call"), dict) else {}
    return [p.external_id, meta.get("call_sid"), phone.get("call_sid")]


def _digest(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
