"""The framework-agnostic client: instructions at call start (with a disk cache, so a sopops outage never
blocks a call) and canonical sessions at call end."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal, Mapping, Protocol, Sequence
from urllib.parse import quote

import httpx

if TYPE_CHECKING:
    from .stream import SessionStream

log = logging.getLogger("sopops")

Source = Literal["live", "cache", "fallback"]


class SopOpsError(Exception):
    """sopops answered with an error, or couldn't be reached. `status` is the HTTP status, None when the request
    never landed."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class SopOpsUnavailable(SopOpsError):
    """No instructions: sopops failed, nothing was cached for this agent, and no fallback was given."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


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
    """Where the agent runs, as the server stores it (`livekit:tonys-pizza`). Read-only: never build it to pass as `agent`."""
    tools: list[str] = field(default_factory=list)
    tool_sops: list[str] = field(default_factory=list)
    source: Source = "live"
    """`live` from sopops, `cache` the last good copy on disk (sopops failed), `fallback` the text you passed."""
    unfilled: tuple[str, ...] = ()
    """Placeholders still in `text`: ones you passed no value for. Empty unless you passed `variables`."""


class InstructionCache(Protocol):
    """Where the last good instructions are kept. `get` and `set` may be plain or async methods."""

    def get(self, key: str) -> Instructions | None | Awaitable[Instructions | None]: ...

    def set(self, key: str, value: Instructions) -> None | Awaitable[None]: ...


class MemoryCache:
    """Keeps instructions in memory for the process's lifetime."""

    def __init__(self) -> None:
        self._items: dict[str, Instructions] = {}

    def get(self, key: str) -> Instructions | None:
        return self._items.get(key)

    def set(self, key: str, value: Instructions) -> None:
        self._items[key] = value


class FileCache:
    """Keeps each agent's last good instructions as a JSON file in `dir` (default `SOPOPS_CACHE_DIR` or
    `~/.cache/sopops`), so they survive restarts. The default cache of `SopOps`."""

    def __init__(self, dir: str | os.PathLike[str] | None = None) -> None:
        self.dir = Path(dir or os.environ.get("SOPOPS_CACHE_DIR") or Path.home() / ".cache" / "sopops")

    def _file(self, key: str) -> Path:
        return self.dir / f"{hashlib.sha256(key.encode()).hexdigest()[:24]}.json"

    def get(self, key: str) -> Instructions | None:
        try:
            return Instructions(**json.loads(self._file(key).read_text()))
        except (OSError, ValueError, TypeError):
            return None

    def set(self, key: str, value: Instructions) -> None:
        """Atomically, so a crash mid-write never leaves a broken copy."""
        path = self._file(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(value), f)
        os.replace(tmp, path)


PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def fill(text: str, variables: Mapping[str, Any]) -> str:
    """`{{placeholders}}` replaced with your values. One with no value is left as it is, the way sopops leaves a
    placeholder it has no value for, so a missing value never mangles the instructions."""
    return PLACEHOLDER.sub(lambda m: str(variables[m.group(1)]) if m.group(1) in variables else m.group(0), text)


def unfilled(text: str) -> tuple[str, ...]:
    """The placeholders still in the text, in order, without repeats."""
    return tuple(dict.fromkeys(PLACEHOLDER.findall(text)))


def _filled(got: Instructions, variables: Mapping[str, Any] | None) -> Instructions:
    """Fill what this call knows, and say what didn't line up. A mismatch is never fatal: the call goes on with the
    placeholder as it is, which is what sopops serves anyway."""
    if not variables:
        return got
    wanted = set(unfilled(got.text))
    text = fill(got.text, variables)
    left = unfilled(text)
    if left:
        log.warning("sopops: no value for %s in %s's instructions; it stays as a placeholder", _names(left), got.agent)
    spare = [k for k in variables if k not in wanted]
    if spare:
        log.warning("sopops: %s has no placeholder in %s's instructions; check the spelling", _names(spare), got.agent)
    return replace(got, text=text, unfilled=left)


def _names(names: Sequence[str]) -> str:
    return ", ".join(f"{{{{{n}}}}}" for n in names)


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


def fetch(
    agent: str,
    release: int | None = None,
    *,
    platform: str | None = None,
    fallback: str | None = None,
    variables: Mapping[str, Any] | None = None,
    url: str | None = None,
    token: str | None = None,
    timeout: float = 2.0,
    cache_dir: str | os.PathLike[str] | None = None,
    cache: InstructionCache | None = None,
) -> Instructions:
    """The agent's instructions, blocking: one call for code that isn't async.

    `agent` is the agent's id on its platform or its sopc id, e.g. `tonys-pizza`. `platform` (`"livekit"`) scopes it
    to one platform when two agents share an id. `release` pins one; without it you get the live release. `url`
    and `token` default to `SOPOPS_URL` and `SOPOPS_TOKEN`, as `SopOps` reads them.

    Falls back the same way `SopOps.fetch_instructions` does: the last good copy on disk, then `fallback`, else
    `SopOpsUnavailable`. Inside async code, await that method on a `SopOps` you keep, so the client is reused.

        ins = sopops.fetch("tonys-pizza")           # the live release
        ins = sopops.fetch("tonys-pizza", 14)       # pinned, e.g. one arm of an A/B test
        ins = sopops.fetch("tonys-pizza", variables={"now": "7:15pm", "caller": "Sam"})   # filled here, not in sopops
        prompt = ins.text
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("sopops.fetch blocks: in async code use `await SopOps(...).fetch_instructions(agent, release)`")
    client = SopOps(url, token, timeout=timeout, cache_dir=cache_dir, cache=cache)
    return asyncio.run(client.fetch_instructions(agent, release, platform=platform, fallback=fallback, variables=variables))


class SopOps:
    """A sopops workspace.

    `url` is the workspace's API URL (`http://sopops.internal:8484/v1/workspaces/prod`), default `SOPOPS_URL`.
    `token` is a workspace token (Settings → Tokens; scope `runtime` to fetch, `ingest` to send sessions), default
    `SOPOPS_TOKEN` (or `SOPOPS_INGEST_TOKEN`).
    `timeout` bounds the fetch at call start; on any failure the last good copy is used. `cache` is where that copy
    is kept (any `InstructionCache`, such as `MemoryCache()`); the default is a `FileCache` in `cache_dir`
    (default `SOPOPS_CACHE_DIR` or `~/.cache/sopops`).
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        *,
        timeout: float = 2.0,
        cache_dir: str | os.PathLike[str] | None = None,
        cache: InstructionCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        url = url or os.environ.get("SOPOPS_URL")
        if not url:
            raise ValueError("pass url= or set SOPOPS_URL to the workspace's API URL, e.g. http://sopops:8484/v1/workspaces/prod")
        self.url = url.rstrip("/")
        self.token = token if token is not None else (os.environ.get("SOPOPS_TOKEN") or os.environ.get("SOPOPS_INGEST_TOKEN"))
        self.timeout = timeout
        self.cache_dir = Path(cache_dir or os.environ.get("SOPOPS_CACHE_DIR") or Path.home() / ".cache" / "sopops")
        self.cache: InstructionCache = cache if cache is not None else FileCache(self.cache_dir)
        self._transport = transport

    def _client(self, timeout: float) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        return httpx.AsyncClient(base_url=self.url, headers=headers, timeout=timeout, transport=self._transport)

    # --- call start ------------------------------------------------------------------------

    async def fetch_instructions(self, agent: str, release: int | None = None, *, platform: str | None = None, fallback: str | None = None,
                                 variables: Mapping[str, Any] | None = None) -> Instructions:
        """The agent's instructions in the live release, or in `release` if pinned. `agent` is the agent's id
        on its platform or its sopc id, e.g. `tonys-pizza`. `platform` (`"livekit"`) scopes it to one platform when two
        agents share an id.

        `variables` fills `{{placeholders}}` this call knows and sopops can't: the time, the caller, anything your
        code works out per call. It happens here, in your process, so those values never reach sopops. A placeholder
        you pass no value for is left as it is and listed in `ins.unfilled`. The cache keeps the unfilled text, so a
        cached copy still fills with this call's values.

        Never waits longer than `timeout`. If sopops fails, returns the last good copy from disk (`source="cache"`),
        else `fallback` (`source="fallback"`), else raises SopOpsUnavailable."""
        params = {k: v for k, v in {"release": str(release) if release is not None else None, "platform": platform}.items() if v is not None}
        try:
            async with self._client(self.timeout) as http:
                res = await asyncio.wait_for(http.get(f"/agents/{quote(agent, safe='')}", params=params), self.timeout + 0.5)
            if res.status_code >= 400:
                raise SopOpsError(f"sopops answered {res.status_code} for agent '{agent}': {_detail(res)}", res.status_code)
            body = res.json()
            got = Instructions(
                text=body["prompt"], release=body["release"], agent=body["agent"], blocks=body.get("components", []),
                hash=body.get("hash"), platform_ref=body.get("platform_ref"), tools=body.get("tools", []), tool_sops=body.get("tool_sops", []),
            )
        except (httpx.HTTPError, asyncio.TimeoutError, SopOpsError, ValueError, KeyError) as e:
            reason = str(e) or type(e).__name__
            cached = await self._read_cache(agent, release, platform)
            if cached is not None:
                log.warning("sopops: using the cached instructions for %s (release %s): %s", agent, cached.release, reason)
                return _filled(cached, variables)
            if fallback is not None:
                log.error("sopops: using the fallback instructions for %s, nothing cached: %s", agent, reason)
                return _filled(Instructions(text=fallback, release=None, agent=agent, source="fallback"), variables)
            raise SopOpsUnavailable(f"no instructions for '{agent}': {reason}") from e
        await self._write_cache(agent, release, platform, got)   # unfilled, so the next call fills it with its own values
        return _filled(got, variables)

    async def fetch_platform_instructions(self, platform: str, agent: str, release: int | None = None, *,
                                          fallback: str | None = None, variables: Mapping[str, Any] | None = None) -> Instructions:
        """The instructions for a raw identifier on one platform.

        Platform adapters call this instead of adding a platform prefix to `agent`. It has the same fallback,
        cache, and variable behavior as `fetch_instructions`. The cache is scoped to `platform`."""
        return await self.fetch_instructions(agent, release, platform=platform, fallback=fallback, variables=variables)

    def _cache_key(self, agent: str, release: int | None, platform: str | None) -> str:
        return f"{self.url}\n{agent}\n{release if release is not None else 'live'}\n{platform or ''}"

    async def _read_cache(self, agent: str, release: int | None, platform: str | None = None) -> Instructions | None:
        try:
            got = self.cache.get(self._cache_key(agent, release, platform))
            if inspect.isawaitable(got):
                got = await got
        except Exception:  # noqa: BLE001 - a cache that can't answer is a cache miss
            return None
        return replace(got, source="cache") if isinstance(got, Instructions) else None

    async def _write_cache(self, agent: str, release: int | None, platform: str | None, got: Instructions) -> None:
        """Failures only log: the call goes on."""
        try:
            done = self.cache.set(self._cache_key(agent, release, platform), got)
            if inspect.isawaitable(done):
                await done
        except Exception as e:  # noqa: BLE001
            log.warning("sopops: couldn't cache instructions for %s: %s", agent, e)

    async def record_served(self, platform: str, call_id: str, agent: str, release: int, *, timeout: float = 3.0) -> None:
        """Tell sopops which release a call ran on once the platform's id for it is known (an ElevenLabs
        conversation id, a Vapi call id), so the session its post-call webhook sends links to that release."""
        async with self._client(timeout) as http:
            res = await http.post("/runtime/served", json={"platform": platform, "call_id": call_id, "agent": agent, "release": release})
        if res.status_code >= 400:
            raise SopOpsError(f"sopops answered {res.status_code}: {_detail(res)}", res.status_code)

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
                    last = SopOpsError(f"couldn't reach sopops: {type(e).__name__}")
                    continue
                if res.status_code == 429 or res.status_code >= 500:
                    last = SopOpsError(f"sopops answered {res.status_code}: {_detail(res)}", res.status_code)
                    continue
                if res.status_code >= 400:
                    raise SopOpsError(f"sopops refused the session ({res.status_code}): {_detail(res)}", res.status_code)
                out: dict[str, Any] = res.json()
                return out
        assert last is not None
        raise last

    # --- during the call (optional: see SessionStream) ------------------------------------------------

    async def open_session(self, session: Mapping[str, Any], *, timeout: float = 3.0) -> dict[str, Any]:
        """Open a session while the call runs, so its events can be sent as they happen: `platform`,
        `external_id` and optionally `channel`, `agent`, `release`, `version_ref`, `started_at`, `metadata`, `test`,
        `config`.
        Returns `{id, created}`. Safe to repeat: keyed by (platform, external_id), like `post_session`."""
        return await self._post("/sessions/open", dict(session), timeout)

    async def append_events(self, platform: str, external_id: str, events: Sequence[Mapping[str, Any]], *, timeout: float = 3.0) -> dict[str, Any]:
        """Add events to an open session: `{seq, type: "turn" | "tool_call", turn | tool_call}`, with `seq` counting
        from 0 per session. Order doesn't matter and a resent `seq` is ignored. Returns `{accepted, duplicates}`.
        The full session sent with `post_session` at the end replaces what was streamed."""
        path = f"/sessions/{quote(platform, safe='')}/{quote(external_id, safe='')}/events"
        return await self._post(path, {"events": [dict(e) for e in events]}, timeout)

    def stream(self, session: Mapping[str, Any], *, flush_interval: float = 0.5, max_batch: int = 50, max_queue: int = 1000,
               on_guidance: Callable[[Mapping[str, Any]], Any] | None = None) -> SessionStream:
        """Stream a session's turns and tool calls while the call runs (see `SessionStream`). It starts sending on
        the first event queued inside a running event loop."""
        from .stream import SessionStream

        return SessionStream(self, session, flush_interval=flush_interval, max_batch=max_batch, max_queue=max_queue, on_guidance=on_guidance)

    async def _post(self, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        """One POST, no retries."""
        try:
            async with self._client(timeout) as http:
                res = await http.post(path, json=body)
        except httpx.HTTPError as e:
            raise SopOpsError(f"couldn't reach sopops: {type(e).__name__}") from e
        if res.status_code >= 400:
            raise SopOpsError(f"sopops answered {res.status_code}: {_detail(res)}", res.status_code)
        out: dict[str, Any] = res.json()
        return out


def _detail(res: httpx.Response) -> str:
    try:
        detail = res.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail) if detail else res.text[:200]
