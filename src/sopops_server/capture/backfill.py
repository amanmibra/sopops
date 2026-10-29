"""Backfill: bring the last few days of calls in from ElevenLabs or Vapi, as if their webhooks had sent them.

For each agent at head on the platform (its `platform_ref`), the calls that started in the window are listed with
the workspace's stored API key, and each one not already in sopops is converted with the same code as the webhook
that would have brought it, so a backfilled session is the same session (same external id, config, test, tool calls
and metadata) and a later webhook for it changes nothing.

- ElevenLabs: `GET /v1/convai/conversations?agent_id=&call_start_after_unix=&page_size=&cursor=` (`conversations`,
  `has_more`, `next_cursor`), then `GET /v1/convai/conversations/<id>` for each new one. That body is the `data` of a
  post-call webhook. Conversations still running (`initiated`, `in-progress`, `processing`) are left for the webhook.
- Vapi: `GET /call?assistantId=&createdAtGt=&limit=` (a plain list, newest first; the next page is `createdAtLt` the
  oldest one seen). Each call is whole, and is converted as an `end-of-call-report`. Calls that haven't ended are left.

It runs in a background thread and keeps its progress in `backfills`: found, imported, skipped (already in sopops),
failed. Requests are spaced out, a 429 waits for `Retry-After` (a few times), and each agent stops after MAX_PAGES
pages. Nothing raises past the run: errors are kept on the agent (or the run) in words that never hold the key.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import quote

import httpx
from sqlmodel import Session, select

from ..auth import actor
from ..errors import workspace_id
from ..insights import Filters
from ..orm import Backfill as BackfillRow
from ..orm import SessionRecord
from ..runtime import crypto
from ..runtime.platforms import NAMES, PlatformError, _headers, base_url
from ..runtime.service import Runtime
from ..schemas import SessionIn
from . import agent_config, vapi
from . import elevenlabs as el

if TYPE_CHECKING:
    from ..jobs import Jobs
    from ..store import Store

log = logging.getLogger("sopops_server.capture.backfill")

MAX_DAYS = 7
PAGE_SIZE = 100  # the most either platform returns in one page
MAX_PAGES = 20  # per agent: 2,000 calls
PAUSE_S = float(os.environ.get("SOPOPS_BACKFILL_PAUSE_SECONDS", "0.2"))  # between requests to the platform
RETRIES_429 = 3
MAX_WAIT_S = 30.0
STALE = timedelta(minutes=15)  # a run that hasn't moved this long stopped with its process
COST_WINDOW = "30d"  # the spend a checking cost estimate is based on
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
EL_RUNNING = ("initiated", "in-progress", "processing")


# --- the conversions the webhooks use (`api.capture`), shared so both make the same session ------------------------


def from_post_call(store: Store, ws: str, body: dict) -> SessionIn | None:
    """An ElevenLabs post-call webhook body as a session, with what the agent ran with as its config."""
    session = el.post_call(body)
    if session is None:
        return None
    agent = store.agent_settings.get(ws, "elevenlabs", (body.get("data") or {}).get("agent_id"))
    session.config = agent_config.elevenlabs(agent, el.call_override(body))
    return session


def from_end_of_call(store: Store, ws: str, message: dict) -> SessionIn | None:
    """A Vapi end-of-call report as a session, with what the assistant ran with as its config."""
    session = vapi.end_of_call(message)
    if session is None:
        return None
    assistant, overrides = vapi.assistant_of(message)
    if assistant is None:
        assistant = store.agent_settings.get(ws, "vapi", vapi.call_of(message).get("assistantId"))
    session.config = agent_config.vapi(assistant, overrides)
    return session


def vapi_report(call: dict) -> dict:
    """A call from `GET /call` in the shape of the `end-of-call-report` Vapi sends for it."""
    artifact = call.get("artifact") if isinstance(call.get("artifact"), dict) else {}
    return {"type": "end-of-call-report", "call": call, "artifact": {**artifact, "messages": artifact.get("messages") or call.get("messages") or []},
            "endedReason": call.get("endedReason"), "startedAt": call.get("startedAt"), "endedAt": call.get("endedAt")}


# --- reading the platform -----------------------------------------------------------------------------------


@dataclass
class Listed:
    agent: str
    platform_id: str
    calls: list[dict] = field(default_factory=list)  # ElevenLabs: summaries; Vapi: whole calls
    error: str | None = None
    capped: bool = False


class Reader:
    """The platform's API with the workspace's key: listing pages, reading one conversation, spaced out."""

    def __init__(self, platform: str, key: str, sleep: Callable[[float], Any] = time.sleep, pause_s: float | None = None):
        self.platform, self.key, self.sleep = platform, key, sleep
        self.pause_s = PAUSE_S if pause_s is None else pause_s
        self.http = httpx.Client(base_url=base_url(platform), timeout=TIMEOUT, headers=_headers(platform, key))
        self._sent = False

    def close(self) -> None:
        self.http.close()

    def get(self, path: str, params: dict | None = None) -> Any:
        name = NAMES[self.platform]
        for attempt in range(RETRIES_429 + 1):
            if self._sent and self.pause_s:
                self.sleep(self.pause_s)
            self._sent = True
            try:
                res = self.http.get(path, params=params)
            except httpx.HTTPError as e:
                raise PlatformError(f"couldn't reach {name}: {type(e).__name__}") from None
            if res.status_code == 429 and attempt < RETRIES_429:
                self.sleep(_retry_after(res.headers.get("retry-after"), attempt))
                continue
            if res.status_code in (401, 403):
                raise PlatformError(f"{name} refused the API key ({res.status_code})")
            if res.status_code == 429:
                raise PlatformError(f"{name} is rate limiting this key (429); try again in a few minutes")
            if res.status_code >= 400:
                detail = res.text[:200].replace(self.key, "…")
                raise PlatformError(f"{name} answered {res.status_code}: {detail}")
            try:
                return res.json()
            except ValueError:
                raise PlatformError(f"{name} returned a response that isn't JSON") from None
        raise AssertionError("unreachable")

    def list_calls(self, platform_id: str, since: datetime) -> tuple[list[dict], bool]:
        """(the finished calls that started since `since`, whether MAX_PAGES stopped the listing)."""
        return self._elevenlabs(platform_id, since) if self.platform == "elevenlabs" else self._vapi(platform_id, since)

    def _elevenlabs(self, agent_id: str, since: datetime) -> tuple[list[dict], bool]:
        out: list[dict] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"agent_id": agent_id, "call_start_after_unix": int(since.timestamp()), "page_size": PAGE_SIZE}
            if cursor:
                params["cursor"] = cursor
            page = self.get("/v1/convai/conversations", params)
            items = page.get("conversations") if isinstance(page, dict) else None
            for c in items or []:
                if isinstance(c, dict) and c.get("conversation_id") and c.get("status") not in EL_RUNNING:
                    out.append(c)
            cursor = page.get("next_cursor") if isinstance(page, dict) else None
            if not (isinstance(page, dict) and page.get("has_more") and cursor):
                return out, False
        return out, True

    def _vapi(self, assistant_id: str, since: datetime) -> tuple[list[dict], bool]:
        out: dict[str, dict] = {}
        before: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"assistantId": assistant_id, "createdAtGt": _iso(since), "limit": PAGE_SIZE}
            if before:
                params["createdAtLt"] = before
            page = self.get("/call", params)
            items = [c for c in (page if isinstance(page, list) else []) if isinstance(c, dict) and c.get("id")]
            for c in items:
                if c.get("status") == "ended" or (not c.get("status") and c.get("endedAt")):
                    out.setdefault(str(c["id"]), c)
            oldest = min((str(c["createdAt"]) for c in items if c.get("createdAt")), default=None)
            if len(items) < PAGE_SIZE or not oldest or oldest == before:
                return list(out.values()), False
            before = oldest
        return list(out.values()), True

    def conversation(self, conversation_id: str) -> dict:
        body = self.get(f"/v1/convai/conversations/{quote(conversation_id, safe='')}")
        if not isinstance(body, dict):
            raise PlatformError("ElevenLabs returned a conversation that isn't an object")
        return body


def _retry_after(value: str | None, attempt: int) -> float:
    try:
        return min(max(float(value or ""), 0.0), MAX_WAIT_S)
    except ValueError:
        return min(2.0 ** (attempt + 1), MAX_WAIT_S)


def call_id(platform: str, call: dict) -> str:
    return str(call.get("conversation_id") if platform == "elevenlabs" else call.get("id"))


# --- the service --------------------------------------------------------------------------------------------


class Backfills:
    def __init__(self, store: Store, jobs: Jobs | None = None, sleep: Callable[[float], Any] = time.sleep):
        self.store, self.jobs, self.sleep = store, jobs, sleep
        self.runtime = Runtime(store)

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    def agents(self, ws: str, platform: str) -> list[tuple[str, str]]:
        """(sop agent, platform id) for each agent at head on the platform, one per platform id."""
        seen: dict[str, str] = {}
        for d in self.runtime.deliveries(ws):
            if d["platform"] == platform and d["platform_id"] and d["platform_id"] not in seen:
                seen[d["platform_id"]] = d["agent"]
        return [(a, pid) for pid, a in seen.items()]

    def _key(self, ws: str, platform: str) -> tuple[str | None, str | None]:
        """(the API key, or why there's none to use)."""
        try:
            key = self.runtime._secret(ws, platform, "api_key")
        except crypto.SecretKeyMissing as e:
            return None, str(e)
        return (key, None) if key else (None, f"No {NAMES[platform]} API key is saved. Add it in Settings, then Platforms.")

    def _list(self, ws: str, platform: str, since: datetime) -> tuple[list[Listed], str | None, Reader | None]:
        """Every connected agent with its calls in the window (or its error), the run's own error, and the reader."""
        agents = [Listed(a, pid) for a, pid in self.agents(ws, platform)]
        key, missing = self._key(ws, platform)
        if key is None:
            for a in agents:
                a.error = missing
            return agents, missing, None
        reader = Reader(platform, key, self.sleep)
        for a in agents:
            try:
                a.calls, a.capped = reader.list_calls(a.platform_id, since)
            except PlatformError as e:
                a.error = _sentence(str(e))
            except Exception as e:  # never past the run
                log.exception("listing %s calls for %s failed", platform, a.platform_id)
                a.error = f"Couldn't list the calls ({type(e).__name__})."
        return agents, None, reader

    def _present(self, ws: str, platform: str, ids: list[str]) -> set[str]:
        out: set[str] = set()
        with self._db() as db:
            for i in range(0, len(ids), 500):
                q = select(SessionRecord.external_id).where(SessionRecord.workspace_id == ws, SessionRecord.platform == platform,
                                                            SessionRecord.external_id.in_(ids[i:i + 500]))  # type: ignore[union-attr]
                out.update(db.exec(q).all())
        return out

    # --- preview ------------------------------------------------------------------------------------------

    def preview(self, workspace: str, platform: str, days: int = MAX_DAYS) -> dict:
        """What a backfill would bring in, read from the platform now: per agent and in all, with an estimate of what
        checking the new sessions costs (from this team's spend per session over the last 30 days)."""
        ws, days = workspace_id(workspace), clamp(days)
        agents, error, reader = self._list(ws, platform, _now() - timedelta(days=days))
        if reader is not None:
            reader.close()
        rows = []
        for a in agents:
            ids = [call_id(platform, c) for c in a.calls]
            present = self._present(ws, platform, ids)
            rows.append({"agent": a.agent, "platform_id": a.platform_id, "found": len(ids), "already_imported": len(present),
                         "to_import": len(ids) - len(present), "capped": a.capped, "error": a.error})
        to_import = sum(r["to_import"] for r in rows)
        per_session = self._cost_per_session(ws)
        return {"platform": platform, "days": days, "key_set": error is None, "error": error, "agents": rows,
                "found": sum(r["found"] for r in rows), "already_imported": sum(r["already_imported"] for r in rows), "to_import": to_import,
                "cost_per_session_usd": per_session, "estimated_cost_usd": round(to_import * per_session, 2) if per_session is not None else None}

    def _cost_per_session(self, ws: str) -> float | None:
        try:
            return self.store.usage.window(Filters.make(ws, COST_WINDOW))["per_session_usd"]
        except Exception:  # an estimate only
            log.exception("couldn't read the spend per session for %s", ws)
            return None

    # --- runs -----------------------------------------------------------------------------------------------

    def latest(self, workspace: str, platform: str) -> dict | None:
        ws = workspace_id(workspace)
        with self._db() as db:
            row = db.exec(select(BackfillRow).where(BackfillRow.workspace == ws, BackfillRow.platform == platform)
                          .order_by(BackfillRow.created_at.desc()).limit(1)).first()  # type: ignore[attr-defined]
        return _out(row) if row else None

    def start(self, workspace: str, platform: str, days: int = MAX_DAYS, run: bool = True) -> dict:
        """Start a backfill in a background thread, or return the one already running for this platform."""
        ws, days = workspace_id(workspace), clamp(days)
        running = self.latest(ws, platform)
        if running and running["status"] == "running":
            return running
        a = actor.get()
        now = _now()
        row = BackfillRow(id=str(uuid.uuid4()), workspace=ws, platform=platform, days=days, status="running", agents=[],
                          created_by=a.name if a else None, created_at=now, updated_at=now)
        with self._db() as db, db.begin():
            self.store.sessions._ensure_workspace(db, ws)
            db.add(row)
        if run:
            threading.Thread(target=self.run, args=(row.id,), daemon=True, name=f"backfill-{row.id[:8]}").start()
        return _out(row)

    def run(self, id: str) -> None:
        """List, read and ingest; each new session goes to the pipeline like a webhook's. Never raises."""
        try:
            with self._db() as db:
                row = db.get(BackfillRow, id)
            if row is None:
                return
            self._run(row)
        except Exception as e:
            log.exception("backfill %s failed", id)
            self._update(id, status="failed", error=f"The backfill stopped: {type(e).__name__}.", finished_at=_now())

    def _run(self, row: BackfillRow) -> None:
        ws, platform = row.workspace, row.platform
        agents, error, reader = self._list(ws, platform, _utc(row.created_at) - timedelta(days=row.days))
        progress = [{"agent": a.agent, "platform_id": a.platform_id, "found": len(a.calls), "imported": 0, "skipped": 0, "failed": 0,
                     "capped": a.capped, "error": a.error} for a in agents]
        self._update(row.id, agents=progress, found=sum(p["found"] for p in progress))
        if reader is None:
            self._update(row.id, status="failed", error=error, finished_at=_now())
            return
        try:
            for a, p in zip(agents, progress):
                ids = [call_id(platform, c) for c in a.calls]
                present = self._present(ws, platform, ids)
                p["skipped"] = len(present)
                self._update(row.id, agents=progress)
                for c, cid in zip(a.calls, ids):
                    if cid in present:
                        continue
                    try:
                        self._import(ws, platform, reader, c, a.platform_id)
                        p["imported"] += 1
                    except PlatformError as e:
                        p["failed"] += 1
                        p["error"] = _sentence(str(e))
                    except Exception as e:
                        log.exception("backfilling %s call %s failed", platform, cid)
                        p["failed"] += 1
                        p["error"] = f"A call couldn't be converted ({type(e).__name__})."
                    self._update(row.id, agents=progress)
        finally:
            reader.close()
        failed = bool(progress) and all(p["error"] for p in progress) and not any(p["imported"] or p["skipped"] for p in progress)
        self._update(row.id, agents=progress, status="failed" if failed else "done", finished_at=_now(),
                     error="Every agent had an error. See each one." if failed else None)

    def _import(self, ws: str, platform: str, reader: Reader, call: dict, platform_id: str) -> None:
        if platform == "elevenlabs":
            data = reader.conversation(str(call["conversation_id"]))
            data.setdefault("agent_id", platform_id)
            session = from_post_call(self.store, ws, {"type": "post_call_transcription", "data": data})
        else:
            session = from_end_of_call(self.store, ws, vapi_report(call))
        if session is None:
            raise PlatformError(f"{NAMES[platform]} returned a call with no id")
        done = self.store.sessions.ingest(ws, session)
        if done.changed and self.jobs is not None:
            self.jobs.process_session(done.ref)

    def _update(self, id: str, agents: list[dict] | None = None, **fields: Any) -> None:
        with self._db() as db, db.begin():
            row = db.get(BackfillRow, id)
            if row is None:
                return
            if agents is not None:
                row.agents = [dict(p) for p in agents]  # a new list, so the JSON column is written
                for k in ("imported", "skipped", "failed"):
                    setattr(row, k, sum(p[k] for p in agents))
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = _now()
            db.add(row)


def clamp(days: int) -> int:
    return max(1, min(int(days), MAX_DAYS))


def _out(row: BackfillRow) -> dict:
    out = row.model_dump()
    if out["status"] == "running" and _now() - _utc(row.updated_at) > STALE:
        out.update(status="failed", error="The backfill stopped when the server restarted. Start it again to pick up the rest.")
    for k in ("created_at", "updated_at", "finished_at"):
        out[k] = _utc(out[k]) if out[k] else None
    return out


def _sentence(text: str) -> str:
    text = text.strip()
    return (text[:1].upper() + text[1:]).rstrip(".") + "." if text else text


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
