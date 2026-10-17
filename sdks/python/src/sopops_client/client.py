"""The framework-agnostic client: instructions at call start (with a disk cache, so a sopops outage never
blocks a call) and canonical sessions at call end."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

import httpx

log = logging.getLogger("sopops_client")

Source = Literal["live", "cache", "fallback"]


class SopopsError(Exception):
    """sopops answered with an error, or couldn't be reached."""


class SopopsUnavailable(SopopsError):
    """No instructions: sopops failed, nothing was cached for this agent, and no fallback was given."""


@dataclass(frozen=True)
class Instructions:
    """An agent's compiled instructions, and what they were built from."""

    text: str
    release: int | None
    agent: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    """Each file the prompt was built from, in prompt order: {kind, id, path, version}."""
    hash: str | None = None
    platform_ref: str | None = None
    """`livekit:tonys-pizza`, `elevenlabs:<agent id>`, `vapi:<assistant id>`."""
    tools: list[str] = field(default_factory=list)
    tool_sops: list[str] = field(default_factory=list)
    source: Source = "live"
    """`live` from sopops, `cache` the last good copy on disk (sopops failed), `fallback` the text you passed."""


def release_pin(metadata: str | Mapping[str, Any] | None) -> int | None:
    """A release pinned for this call (A/B tests) in job or room metadata: `{"sopops_release": 3}` or
    `{"sopops": {"release": 3}}`, as a JSON string or a dict. None if there's none."""
    if not metadata:
        return None
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            return None
    if not isinstance(metadata, Mapping):
        return None
    nested = metadata.get("sopops")
    value = metadata.get("sopops_release", nested.get("release") if isinstance(nested, Mapping) else None)
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


class Sopops:
    """A sopops workspace.

    `url` is the workspace's API URL (`http://sopops.internal:8484/v1/workspaces/prod`), default `SOPOPS_URL`.
    `token` is its runtime token (Settings → Ingest token), default `SOPOPS_TOKEN` (or `SOPOPS_INGEST_TOKEN`).
    `timeout` bounds the fetch at call start; on any failure the last good copy on disk is used
    (`cache_dir`, default `SOPOPS_CACHE_DIR` or `~/.cache/sopops`).
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        *,
        timeout: float = 2.0,
        cache_dir: str | os.PathLike[str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        url = url or os.environ.get("SOPOPS_URL")
        if not url:
            raise ValueError("pass url= or set SOPOPS_URL to the workspace's API URL, e.g. http://sopops:8484/v1/workspaces/prod")
        self.url = url.rstrip("/")
        self.token = token if token is not None else (os.environ.get("SOPOPS_TOKEN") or os.environ.get("SOPOPS_INGEST_TOKEN"))
        self.timeout = timeout
        self.cache_dir = Path(cache_dir or os.environ.get("SOPOPS_CACHE_DIR") or Path.home() / ".cache" / "sopops")
        self._transport = transport

    def _client(self, timeout: float) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        return httpx.AsyncClient(base_url=self.url, headers=headers, timeout=timeout, transport=self._transport)

    # --- call start ------------------------------------------------------------------------

    async def fetch_instructions(self, agent: str, release: int | None = None, *, fallback: str | None = None) -> Instructions:
        """The agent's instructions in the live release, or in `release` if pinned. `agent` is the sopc agent id
        or its platform ref (`livekit:tonys-pizza`).

        Never waits longer than `timeout`. If sopops fails, returns the last good copy from disk (`source="cache"`),
        else `fallback` (`source="fallback"`), else raises SopopsUnavailable."""
        params = {"release": str(release)} if release is not None else None
        try:
            async with self._client(self.timeout) as http:
                res = await asyncio.wait_for(http.get(f"/agents/{agent}", params=params), self.timeout + 0.5)
            if res.status_code >= 400:
                raise SopopsError(f"sopops answered {res.status_code} for agent '{agent}': {_detail(res)}")
            body = res.json()
            got = Instructions(
                text=body["prompt"], release=body["release"], agent=body["agent"], blocks=body.get("components", []),
                hash=body.get("hash"), platform_ref=body.get("platform_ref"), tools=body.get("tools", []), tool_sops=body.get("tool_sops", []),
            )
        except (httpx.HTTPError, asyncio.TimeoutError, SopopsError, ValueError, KeyError) as e:
            reason = str(e) or type(e).__name__
            cached = self._read_cache(agent, release)
            if cached is not None:
                log.warning("sopops: using the cached instructions for %s (release %s): %s", agent, cached.release, reason)
                return cached
            if fallback is not None:
                log.error("sopops: using the fallback instructions for %s, nothing cached: %s", agent, reason)
                return Instructions(text=fallback, release=None, agent=agent, source="fallback")
            raise SopopsUnavailable(f"no instructions for '{agent}': {reason}") from e
        self._write_cache(agent, release, got)
        return got

    def _cache_file(self, agent: str, release: int | None) -> Path:
        key = hashlib.sha256(f"{self.url}\n{agent}\n{release if release is not None else 'live'}".encode()).hexdigest()[:24]
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, agent: str, release: int | None) -> Instructions | None:
        try:
            data = json.loads(self._cache_file(agent, release).read_text())
            return Instructions(**{**data, "source": "cache"})
        except (OSError, ValueError, TypeError):
            return None

    def _write_cache(self, agent: str, release: int | None, got: Instructions) -> None:
        """Atomically, so a crash mid-write never leaves a broken copy. Failures only log: the call goes on."""
        path = self._cache_file(agent, release)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
            with os.fdopen(fd, "w") as f:
                json.dump(asdict(got), f)
            os.replace(tmp, path)
        except OSError as e:
            log.warning("sopops: couldn't cache instructions for %s: %s", agent, e)

    async def record_served(self, platform: str, call_id: str, agent: str, release: int, *, timeout: float = 3.0) -> None:
        """Tell sopops which release a call ran on once the platform's id for it is known (an ElevenLabs
        conversation id, a Vapi call id), so the session its post-call webhook sends links to that release."""
        async with self._client(timeout) as http:
            res = await http.post("/runtime/served", json={"platform": platform, "call_id": call_id, "agent": agent, "release": release})
        if res.status_code >= 400:
            raise SopopsError(f"sopops answered {res.status_code}: {_detail(res)}")

    # --- call end ----------------------------------------------------------------------------

    async def post_session(self, session: Mapping[str, Any], *, retries: int = 2, timeout: float = 5.0) -> dict[str, Any]:
        """Send one finished session in sopops's canonical format (`platform`, `external_id`, `turns`, ...).
        Safe to repeat: sopops upserts on (platform, external_id). Retries connection errors, 429 and 5xx."""
        last: Exception | None = None
        async with self._client(timeout) as http:
            for attempt in range(retries + 1):
                if attempt:
                    await asyncio.sleep(0.5 * 2 ** (attempt - 1))
                try:
                    res = await http.post("/sessions", json=dict(session))
                except httpx.HTTPError as e:
                    last = SopopsError(f"couldn't reach sopops: {type(e).__name__}")
                    continue
                if res.status_code == 429 or res.status_code >= 500:
                    last = SopopsError(f"sopops answered {res.status_code}: {_detail(res)}")
                    continue
                if res.status_code >= 400:
                    raise SopopsError(f"sopops refused the session ({res.status_code}): {_detail(res)}")
                out: dict[str, Any] = res.json()
                return out
        assert last is not None
        raise last


def _detail(res: httpx.Response) -> str:
    try:
        detail = res.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail) if detail else res.text[:200]
