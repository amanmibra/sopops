"""Runtime delivery: how each agent gets its prompt, pushing releases to ElevenLabs and Vapi, serving their
call-start webhooks, and the runtime-scoped tokens that guard prompt fetches.

Credentials are per workspace and encrypted (see `crypto`); they're decrypted only to call the platform or check a
webhook, and never returned or logged.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Iterable, Mapping

from sqlmodel import Session, delete, select

from ..auth import mode
from ..errors import NotFound, workspace_id
from ..orm import AgentDelivery, PlatformSecret, PushResult, RuntimeServe
from ..tokens import RUNTIME, Forbidden, Unauthorized
from . import crypto, platforms
from .platforms import NAMES, PUSHABLE, PlatformError

if TYPE_CHECKING:
    from ..store import Store

log = logging.getLogger("sopops.runtime")

MODES = ("pull", "push", "both")
SERVE_TTL = timedelta(days=7)  # a session arrives within minutes of its call; older pending rows are dropped


class BadRequest(ValueError):
    pass


def require_webhook_secret() -> bool:
    """`SOPOPS_REQUIRE_WEBHOOK_SECRET` (default false; set it true when sopops is hosted): call-start webhooks are
    refused for a workspace with neither a webhook secret for the platform nor a runtime token, instead of open."""
    return os.environ.get("SOPOPS_REQUIRE_WEBHOOK_SECRET", "").strip().lower() in ("1", "true", "yes", "on")


def split_ref(platform_ref: str) -> tuple[str, str]:
    platform, _, pid = platform_ref.partition(":")
    return platform, pid


class Runtime:
    def __init__(self, store: Store):
        self.store = store

    def _db(self) -> Session:
        return Session(self.store.engine, expire_on_commit=False)

    # --- runtime tokens -----------------------------------------------------------------

    def check_fetch(self, workspace: str, authorization: str | None) -> None:
        """Prompt fetches take a token with the `runtime` scope. A workspace without an unrevoked runtime token is in
        local mode: fetches are open, unless sign-in is on (then the token is always needed)."""
        if mode.required() or self._has_token(workspace):
            self.store.tokens.check(workspace, authorization, RUNTIME)

    def _has_token(self, workspace: str) -> bool:
        return self.store.tokens.guarded(workspace, RUNTIME)

    # --- delivery mode -----------------------------------------------------------------

    def deliveries(self, workspace: str) -> list[dict]:
        """Every agent at head with its platform and delivery mode."""
        ws = workspace_id(workspace)
        agents = self.store.items(ws)["agents"]
        with self._db() as db:
            modes = {r.agent: r.mode for r in db.exec(select(AgentDelivery).where(AgentDelivery.workspace == ws))}
        return [
            {
                "agent": a["id"],
                "platform": a["platform"],
                "platform_id": a["platform_id"],
                "mode": modes.get(a["id"], "pull"),
                "modes": list(MODES) if a["platform"] in PUSHABLE else ["pull"],
            }
            for a in sorted(agents, key=lambda a: a["id"])
        ]

    def set_delivery(self, workspace: str, agent: str, mode: str) -> dict:
        ws = workspace_id(workspace)
        row = next((d for d in self.deliveries(ws) if d["agent"] == agent), None)
        if row is None:
            raise NotFound(f"agent '{agent}' not found")
        if mode not in row["modes"]:
            raise BadRequest(f"{row['platform']} agents can only pull their prompt; sopops can push to {', '.join(NAMES.values())}")
        with self._db() as db, db.begin():
            existing = db.get(AgentDelivery, (ws, agent))
            if existing:
                existing.mode = mode
            else:
                db.add(AgentDelivery(workspace=ws, agent=agent, mode=mode))
        return {**row, "mode": mode}

    def _modes(self, ws: str) -> dict[str, str]:
        with self._db() as db:
            return {r.agent: r.mode for r in db.exec(select(AgentDelivery).where(AgentDelivery.workspace == ws))}

    # --- credentials ---------------------------------------------------------------------

    def credentials(self, workspace: str) -> list[dict]:
        ws = workspace_id(workspace)
        with self._db() as db:
            rows = {(r.platform, r.kind): r for r in db.exec(select(PlatformSecret).where(PlatformSecret.workspace == ws))}

        def info(platform: str, kind: str) -> dict:
            r = rows.get((platform, kind))
            return {"set": r is not None, "hint": r.hint if r else None, "created_at": _utc(r.created_at) if r else None}

        return [{"platform": p, "api_key": info(p, "api_key"), "webhook_secret": info(p, "webhook_secret")} for p in PUSHABLE]

    def set_secret(self, workspace: str, platform: str, kind: str, value: str | None) -> dict:
        """Store a credential (encrypted). A webhook secret without a value is generated, and returned this once."""
        ws = workspace_id(workspace)
        _check_platform(platform)
        generated = None
        if not value or not value.strip():
            if kind != "webhook_secret":
                raise BadRequest("enter the API key")
            value = generated = "whsec_" + secrets.token_urlsafe(32)
        value = value.strip()
        row = PlatformSecret(workspace=ws, platform=platform, kind=kind, ciphertext=crypto.encrypt(value), hint=crypto.hint(value), created_at=_now())
        with self._db() as db, db.begin():
            self.store.sessions._ensure_workspace(db, ws)
            old = db.get(PlatformSecret, (ws, platform, kind))
            if old is not None:
                db.delete(old)
                db.flush()
            db.add(row)
        return {"platform": platform, "kind": kind, "hint": row.hint, "created_at": row.created_at, "secret": generated}

    def delete_secret(self, workspace: str, platform: str, kind: str) -> None:
        ws = workspace_id(workspace)
        _check_platform(platform)
        with self._db() as db, db.begin():
            row = db.get(PlatformSecret, (ws, platform, kind))
            if row is not None:
                db.delete(row)

    def _secret(self, ws: str, platform: str, kind: str) -> str | None:
        with self._db() as db:
            row = db.get(PlatformSecret, (ws, platform, kind))
        return crypto.decrypt(row.ciphertext) if row else None

    def test_key(self, workspace: str, platform: str, platform_id: str | None = None) -> dict:
        """Read one agent with the stored key (read-only): the given id, else the first agent at head on that platform."""
        ws = workspace_id(workspace)
        _check_platform(platform)
        key = self._secret(ws, platform, "api_key")
        if key is None:
            raise BadRequest(f"no {NAMES[platform]} API key is stored")
        if not platform_id:
            platform_id = next((d["platform_id"] for d in self.deliveries(ws) if d["platform"] == platform), None)
        if not platform_id:
            raise BadRequest(f"no agent on {NAMES[platform]} to test with; add one, or name an agent id")
        try:
            agent = platforms.get_agent(platform, key, platform_id)
        except PlatformError as e:
            return {"ok": False, "platform_id": platform_id, "message": _sentence(str(e))}
        prompt = platforms.live_prompt(platform, agent)
        name = agent.get("name") or platform_id
        words = f"its prompt has {len(prompt)} characters" if prompt is not None else "its prompt isn't a single system prompt"
        return {"ok": True, "platform_id": platform_id, "message": f"Read {NAMES[platform]} agent '{name}': {words}."}

    # --- push ------------------------------------------------------------------------------

    def push_release(self, workspace: str, number: int, agents: Iterable[str] | None = None, only_failed: bool = False) -> list[dict]:
        """Push each push-mode agent's prompt in release `number` to its platform, recording each result.
        `agents` limits it to those; `only_failed` to agents whose last push failed (a retry)."""
        ws = workspace_id(workspace)
        rel = self.store.get_release(ws, number)
        modes = self._modes(ws)
        wanted = set(agents) if agents is not None else None
        previous = {r["agent"]: r for r in self.push_results(ws, number)}
        for agent_id, built in sorted(rel["build"]["agents"].items()):
            platform, pid = split_ref(built["platform_ref"])
            if platform not in PUSHABLE or modes.get(agent_id, "pull") == "pull":
                continue
            if wanted is not None and agent_id not in wanted:
                continue
            if only_failed and previous.get(agent_id, {}).get("status") != "error":
                continue
            status, version_id, error = "error", None, None
            try:
                key = self._secret(ws, platform, "api_key")
                if key is None:
                    raise PlatformError(f"no {NAMES[platform]} API key in Settings")
                done = platforms.push(platform, key, pid, built["prompt"])
                status, version_id = done.status, done.version_id
            except (PlatformError, crypto.SecretKeyMissing) as e:
                error = _sentence(str(e))
            except Exception as e:  # never let one agent stop the rest, or leak details
                log.exception("push of %s in %s release %s failed", agent_id, ws, number)
                error = f"Unexpected error ({type(e).__name__})."
            self._record(ws, number, agent_id, platform, pid, status, version_id, error)
            log.info("pushed %s release %s to %s: %s", agent_id, number, NAMES[platform], status)
        return self.push_results(ws, number)

    def _record(self, ws, number, agent, platform, pid, status, version_id, error) -> None:
        with self._db() as db, db.begin():
            row = db.get(PushResult, (ws, number, agent))
            if row is None:
                db.add(PushResult(workspace=ws, release=number, agent=agent, platform=platform, platform_id=pid, status=status,
                                  version_id=version_id, error=error, attempts=1, at=_now()))
            else:
                row.platform, row.platform_id, row.status, row.version_id, row.error = platform, pid, status, version_id, error
                row.attempts += 1
                row.at = _now()

    def push_results(self, workspace: str, number: int) -> list[dict]:
        ws = workspace_id(workspace)
        with self._db() as db:
            rows = db.exec(select(PushResult).where(PushResult.workspace == ws, PushResult.release == number).order_by(PushResult.agent))
            return [{**r.model_dump(exclude={"workspace"}), "at": _utc(r.at)} for r in rows]

    def pushes_pending(self, workspace: str) -> bool:
        """Whether any agent is set to push (so a publish has something to do)."""
        return any(m != "pull" for m in self._modes(workspace).values())

    # --- call-start webhooks (pull) --------------------------------------------------------

    def check_webhook(self, workspace: str, platform: str, headers: Mapping[str, str], raw: bytes) -> None:
        """Accept the platform's webhook secret (as a bearer token, a secret header, or an HMAC signature) or the
        a runtime-scoped token. Open only when the workspace has neither (local mode), and never with
        SOPOPS_REQUIRE_WEBHOOK_SECRET set."""
        ws = workspace_id(workspace)
        secret = self._secret(ws, platform, "webhook_secret")
        has_token = self._has_token(ws)
        if secret is None and not has_token:
            if require_webhook_secret():
                raise Unauthorized(
                    f"this workspace has no {NAMES[platform]} webhook secret (Settings → Platforms) or runtime token, and this "
                    "sopops requires one (SOPOPS_REQUIRE_WEBHOOK_SECRET)"
                )
            return
        h = {k.lower(): v for k, v in headers.items()}
        scheme, _, bearer = h.get("authorization", "").partition(" ")
        bearer = bearer.strip() if scheme.lower() == "bearer" else ""
        if secret:
            plain = h.get("x-sopops-secret") or h.get("x-vapi-secret") or bearer
            if plain and hmac.compare_digest(plain.encode(), secret.encode()):
                return
            if platform == "elevenlabs" and "elevenlabs-signature" in h and _elevenlabs_signed(h["elevenlabs-signature"], raw, secret):
                return
            if platform == "vapi" and "x-signature" in h and _vapi_signed(h["x-signature"], h.get("x-timestamp", ""), raw, secret):
                return
        if has_token and bearer:
            try:
                self.store.tokens.check(ws, f"Bearer {bearer}", RUNTIME)
                return
            except (Unauthorized, Forbidden):
                pass
        raise Unauthorized(f"the {NAMES[platform]} webhook isn't signed with this workspace's webhook secret (Settings → Platforms)")

    def resolve(self, workspace: str, platform: str, ref: str | None, release: int | None) -> dict:
        """The served build for an agent named by sopc id, platform ref, or its id on `platform`. Without `ref`,
        the only agent on that platform in the release."""
        rel = self.store.get_release(workspace, release)
        built = rel["build"]["agents"]
        on_platform = [a for a, b in built.items() if split_ref(b["platform_ref"])[0] == platform]
        if ref:
            match = ref if ref in built else next((a for a in on_platform if built[a]["platform_ref"] in (ref, f"{platform}:{ref}")), None)
        else:
            match = on_platform[0] if len(on_platform) == 1 else None
            if len(on_platform) > 1:
                raise BadRequest(f"release {rel['number']} has {len(on_platform)} {NAMES[platform]} agents; add ?agent=<id> to the webhook URL")
        if match is None:
            raise NotFound(f"no {NAMES[platform]} agent '{ref or ''}' in release {rel['number']}")
        return {"agent": match, "release": rel["number"], **built[match]}

    def record_serve(self, workspace: str, platform: str, call_ids: Iterable[str | None], served: dict, log_fetch: bool = True) -> None:
        """Log the fetch (unless the client already fetched it), and remember the release per call id for the
        session that arrives later."""
        ws = workspace_id(workspace)
        if log_fetch:
            self.store.log_fetch(ws, served["agent"], served["release"], served["hash"])
        ids = [c for c in dict.fromkeys(call_ids) if c]
        if not ids:
            return
        now = _now()
        with self._db() as db, db.begin():
            self.store.sessions._ensure_workspace(db, ws)
            db.execute(delete(RuntimeServe).where(RuntimeServe.workspace == ws, RuntimeServe.at < now - SERVE_TTL))
            for cid in ids:
                row = db.get(RuntimeServe, (ws, platform, cid[:256]))
                if row is None:
                    db.add(RuntimeServe(workspace=ws, platform=platform, call_id=cid[:256], agent=served["agent"], release=served["release"], hash=served["hash"], at=now))
                else:
                    row.agent, row.release, row.hash, row.at = served["agent"], served["release"], served["hash"], now


def served_release(db: Session, ws: str, platform: str, keys: Iterable[str | None]) -> int | None:
    """The release a call-start webhook served for any of these call ids (used at ingest)."""
    ids = [k for k in keys if isinstance(k, str) and k]
    if not ids:
        return None
    q = select(RuntimeServe.release).where(RuntimeServe.workspace == ws, RuntimeServe.platform == platform, RuntimeServe.call_id.in_(ids))
    return db.exec(q).first()


def _elevenlabs_signed(header: str, raw: bytes, secret: str) -> bool:
    """`t=<unix secs>,v0=<hex HMAC-SHA256(secret, "<t>.<body>")>`, at most 30 minutes old (as ElevenLabs' SDK checks)."""
    try:
        parts = dict(p.strip().split("=", 1) for p in header.split(","))
        t = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if t < time.time() - 1800:
        return False
    want = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(parts.get("v0", ""), want)


def _vapi_signed(signature: str, timestamp: str, raw: bytes, secret: str) -> bool:
    """Vapi's HMAC credential with its defaults: sha256, hex, over `"{timestamp}.{body}"`. A timestamp more than
    5 minutes off is refused."""
    try:
        ts = float(timestamp)
    except ValueError:
        return False
    secs = ts / 1000 if ts > 1e11 else ts
    if abs(time.time() - secs) > 300:
        return False
    want = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
    got = signature.split("=", 1)[1] if signature.startswith("sha256=") else signature
    return hmac.compare_digest(got.strip(), want)


def _check_platform(platform: str) -> None:
    if platform not in PUSHABLE:
        raise NotFound(f"unknown platform '{platform}'; sopops stores keys for {', '.join(PUSHABLE)}")


def _sentence(text: str) -> str:
    text = text.strip()
    return (text[:1].upper() + text[1:]).rstrip(".") + "." if text else text


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


class AssistantCache:
    """Vapi assistants read for `assistant-request` (to override `model` with the new system prompt), kept briefly
    so a burst of calls doesn't read the assistant each time."""

    TTL = 300.0

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], tuple[float, dict]] = {}

    def model_with_prompt(self, runtime: Runtime, ws: str, pid: str, prompt: str) -> dict | None:
        """The assistant's model with `prompt` as its system message, or None if it can't be read quickly."""
        hit = self._items.get((ws, pid))
        if hit is None or hit[0] < time.monotonic():
            try:
                key = runtime._secret(ws, "vapi", "api_key")
                if key is None:
                    return None
                assistant = platforms.get_agent("vapi", key, pid, timeout=platforms.httpx.Timeout(2.5, connect=1.5))
            except (PlatformError, crypto.SecretKeyMissing) as e:
                log.warning("assistant-request: couldn't read Vapi assistant %s: %s", pid, e)
                return None
            hit = (time.monotonic() + self.TTL, assistant)
            self._items[(ws, pid)] = hit
        try:
            return platforms.vapi_model_with_prompt(hit[1], prompt)
        except PlatformError as e:
            log.warning("assistant-request: %s", e)
            return None
