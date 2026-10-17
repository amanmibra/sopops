"""Sessions: ingest (an idempotent upsert on workspace + platform + external id), listing, and ingest tokens.

A session is one conversation an agent had: a voice call or a text chat. Every write publishes a session event
(see `events`), and ingest hands the session to the pipeline (see `jobs`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, or_, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .events import CHANNEL, Broker
from .models import IngestToken, Release, SessionRecord, Workspace
from .pipeline.models import SessionRef
from .pipeline.normalize import to_utc
from .schemas import SessionIn

if TYPE_CHECKING:
    from .store import Store

TOKEN_PREFIX = "sopops_"


class Unauthorized(Exception):
    pass


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
        from .store import _ws

        ws = _ws(workspace)
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
                from .runtime.service import served_release

                release = served_release(db, ws, p.platform, _call_keys(p))
            if release is not None and db.get(Release, (ws, release)) is None:
                warnings.append(f"Release {release} doesn't exist in workspace '{ws}'; the session is kept without it.")
                release = None
            against = release or db.exec(select(Workspace.current_release).where(Workspace.workspace == ws)).first()
            agent_id = self._match_agent(db, ws, p.agent, p.platform, against) if p.agent and against else None
            if p.agent and against and agent_id is None:
                warnings.append(f"No agent '{p.agent}' in release {against}; the session keeps the name as sent.")
            # a turn can't come before the session started (a helper may note the start a moment late)
            started = min((t for t in (to_utc(p.started_at), _first_time(p)) if t), default=now)
            fields = dict(
                agent_id=agent_id,
                agent_ref=p.agent,
                channel=p.channel,
                release_id=release,
                version_ref=p.version_ref,
                started_at=started,
                ended_at=to_utc(p.ended_at),
                status="partial" if p.partial else "received",
                status_reason="The sender marked the transcript incomplete." if p.partial else None,
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
            self._emit(db, {"type": "created" if created else "updated", "workspace": ws, "id": row.id, "status": row.status})
            return Ingested(row.id, created, not created, row.status, warnings, SessionRef(workspace_id=ws, session_id=row.id, revision=digest))

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
        from .store import NotFound, _ws

        with self._db() as db:
            row = db.exec(select(SessionRecord).where(SessionRecord.workspace_id == _ws(workspace), SessionRecord.id == id)).first()
        if row is None:
            raise NotFound(f"session '{id}' not found")
        return row

    def get(self, workspace: str, id: str) -> dict:
        row = self.load(workspace, id)
        return {**_summary(row), "turns": row.turns, "tool_calls": row.tool_calls, "metadata": row.meta, "checks": self.store.checks.for_session(row.id)}

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
    ) -> dict:
        """Newest first (by start time). `agent` matches the sopc agent or the agent as sent. The drill-down filters
        (procedure, its result, rule and status, pattern, theme, any finding) each narrow to sessions with such a
        check, rule result or finding; a rule pattern includes its sub-patterns, a theme every pattern in it."""
        from .models import Finding, Pattern, ProcedureCheck, RuleResult
        from .store import _ws

        S = SessionRecord
        q = select(S).where(S.workspace_id == _ws(workspace))
        if release is not None:
            q = q.where(S.release_id == release)
        if procedure or procedure_result:
            pc = select(ProcedureCheck.session_id)
            if procedure:
                pc = pc.where(ProcedureCheck.procedure_id == procedure)
            if procedure_result:
                pc = pc.where(ProcedureCheck.result == procedure_result)
            q = q.where(S.id.in_(pc))
        if rule:
            rr = select(RuleResult.session_id).where(RuleResult.rule_id == rule)
            if procedure:
                rr = rr.where(RuleResult.procedure_id == procedure)
            if rule_status:
                rr = rr.where(RuleResult.status.in_(rule_status))
            q = q.where(S.id.in_(rr))
        if pattern or theme:
            pats = select(Pattern.id).where(Pattern.workspace_id == _ws(workspace))
            pats = pats.where(or_(Pattern.id == pattern, Pattern.parent_id == pattern)) if pattern else pats.where(Pattern.theme_id == theme)
            q = q.where(S.id.in_(select(Finding.session_id).where(Finding.pattern_id.in_(pats))))
        if eligible is not None:  # Insights' eligible sessions: checked (or partial) with a result
            ok = and_(S.status.in_(("checked", "partial")), S.result.is_not(None))
            q = q.where(ok if eligible else ~ok)
        if finding is not None:
            has = S.id.in_(select(Finding.session_id).where(Finding.workspace_id == _ws(workspace)))
            q = q.where(has if finding else ~has)
        if agent:
            q = q.where(or_(S.agent_id == agent, S.agent_ref == agent))
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
        return {"items": [_summary(r) for r in rows], "next_cursor": _encode_cursor(rows[-1]) if more else None}

    # --- ingest tokens ---------------------------------------------------------

    def token_info(self, workspace: str) -> dict:
        from .store import _ws

        with self._db() as db:
            row = db.get(IngestToken, _ws(workspace))
        if row is None:
            return {"exists": False, "prefix": None, "created_at": None}
        return {"exists": True, "prefix": row.prefix, "created_at": _utc(row.created_at)}

    def rotate_token(self, workspace: str) -> dict:
        """A new token for the workspace; the old one stops working. The token itself is only returned here."""
        from .store import _ws

        ws = _ws(workspace)
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        row = IngestToken(workspace=ws, token_hash=_hash(token), prefix=token[: len(TOKEN_PREFIX) + 6], created_at=_now())
        with self._db() as db, db.begin():
            self._ensure_workspace(db, ws)
            old = db.get(IngestToken, ws)
            if old is not None:
                db.delete(old)
                db.flush()
            db.add(row)
        return {"token": token, "prefix": row.prefix, "created_at": row.created_at}

    def revoke_token(self, workspace: str) -> None:
        from .store import _ws

        with self._db() as db, db.begin():
            old = db.get(IngestToken, _ws(workspace))
            if old is not None:
                db.delete(old)

    def check_token(self, workspace: str, authorization: str | None) -> None:
        """Raise Unauthorized unless `authorization` is `Bearer <the workspace's ingest token>`."""
        from .store import _ws

        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise Unauthorized("send the workspace's ingest token as 'Authorization: Bearer <token>' (create one in Settings)")
        with self._db() as db:
            row = db.get(IngestToken, _ws(workspace))
        if row is None:
            raise Unauthorized(f"workspace '{workspace}' has no ingest token; create one in Settings")
        if not secrets.compare_digest(row.token_hash, _hash(token.strip())):
            raise Unauthorized("invalid ingest token")

    # --- internals -------------------------------------------------------------

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    def _ensure_workspace(self, db: Session, ws: str) -> None:
        dialect = postgresql if self.postgres else sqlite
        db.execute(dialect.insert(Workspace.__table__).values(workspace=ws).on_conflict_do_nothing())

    def _emit(self, db: Session, event: dict) -> None:
        """Postgres: NOTIFY in this transaction (delivered on commit, to every process). SQLite: publish after commit."""
        if self.postgres:
            db.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": json.dumps(event)})
        else:
            from sqlalchemy import event as sa_event

            sa_event.listen(db, "after_commit", lambda _s: self.broker.publish(event), once=True)

    def _match_agent(self, db: Session, ws: str, agent: str, platform: str, number: int) -> str | None:
        """The sopc agent `agent` names in the release: its sopc id, its platform ref (`livekit:tonys-pizza`),
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


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime) -> datetime:
    return at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _call_keys(p: SessionIn) -> list:
    """Ids a call-start webhook may have keyed this call by: the session's own id, or a phone call's sid."""
    meta = p.metadata or {}
    phone = meta.get("phone_call") if isinstance(meta.get("phone_call"), dict) else {}
    return [p.external_id, meta.get("call_sid"), phone.get("call_sid")]
