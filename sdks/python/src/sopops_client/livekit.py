"""LiveKit Agents adapter (`pip install "sopops-client[livekit]"`).

    from sopops_client.livekit import attach

    async def entrypoint(ctx: JobContext):
        await ctx.connect()
        session = AgentSession(...)
        ins = await attach(ctx, session, agent="tonys-pizza")
        await session.start(room=ctx.room, agent=Agent(instructions=ins.text))

`attach` fetches the instructions (honoring a release pinned in job or room metadata), keeps the release in
`session.userdata`, and at shutdown posts the session (`session.history` with timestamps and tool calls) to
sopops. Nothing here imports livekit at import time; the objects are used through their documented attributes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping

from .client import Instructions, Sopops, release_pin

if TYPE_CHECKING:  # only for type checkers: the adapter needs no livekit import at runtime
    from livekit.agents import AgentSession, JobContext

log = logging.getLogger("sopops_client.livekit")

RELEASE_KEY = "sopops_release"
AGENT_KEY = "sopops_agent"


def from_livekit(history: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map a LiveKit `ChatContext` (`session.history`), or its `to_dict(exclude_timestamp=False)`, to sopops
    `(turns, tool_calls)`. Times are Unix seconds (`at`). A message that repeats the one before it is dropped
    (LiveKit can add a user transcript twice)."""
    data = history.to_dict(exclude_timestamp=False) if hasattr(history, "to_dict") else history
    turns: list[dict[str, Any]] = []
    tools: dict[str, dict[str, Any]] = {}
    for item in data.get("items", []):
        kind, at = item.get("type"), item.get("created_at")
        if kind == "message":
            text = " ".join(c for c in item.get("content", []) if isinstance(c, str)).strip()
            role = item.get("role")
            if turns and turns[-1]["role"] == role and turns[-1]["text"] == text and turns[-1].get("name") is None:
                continue
            turns.append({"role": role, "text": text, "at": at, "interrupted": bool(item.get("interrupted", False))})
        elif kind == "function_call":
            name, call_id = item.get("name"), item.get("call_id")
            turns.append({"role": "tool", "text": f"{name}({item.get('arguments', '')})", "name": name, "at": at})
            tools[call_id] = {"name": name, "arguments": item.get("arguments"), "call_id": call_id, "at": at}
        elif kind == "function_call_output" and item.get("call_id") in tools:
            tools[item["call_id"]].update(result=item.get("output"), is_error=bool(item.get("is_error", False)))
    return turns, list(tools.values())


def pinned_release(ctx: JobContext) -> int | None:
    """A release pinned for this call in the job's dispatch metadata, else the room's metadata."""
    job = getattr(ctx, "job", None)
    room = getattr(ctx, "room", None)
    return release_pin(getattr(job, "metadata", None)) or release_pin(getattr(room, "metadata", None))


async def attach(
    ctx: JobContext,
    session: AgentSession[Any],
    agent: str | None = None,
    *,
    client: Sopops | None = None,
    release: int | None = None,
    fallback: str | None = None,
) -> Instructions:
    """Fetch the agent's instructions, note the release in `session.userdata`, and post the session at shutdown.

    `agent`: the sopc agent id or platform ref; default `livekit:<the job's agent name>`. `release` pins one;
    default: a pin in job or room metadata, else the live release. `fallback`: text to use if sopops is down and
    nothing is cached (see `Sopops.fetch_instructions`). Call it after `ctx.connect()` for room metadata."""
    client = client or Sopops()
    agent = agent or f"livekit:{ctx.job.agent_name}"
    pin = release if release is not None else pinned_release(ctx)
    ins = await client.fetch_instructions(agent, pin, fallback=fallback)
    _remember(session, ins)
    record_session(ctx, session, client=client, agent=ins.agent)
    return ins


def record_session(ctx: JobContext, session: AgentSession[Any], *, client: Sopops | None = None, agent: str | None = None) -> None:
    """Post `session.history` to sopops when the job shuts down, keyed by the job id (rooms get reused), with the
    release from `session.userdata`. Errors are logged, never raised into shutdown."""
    client = client or Sopops()
    started = datetime.now(timezone.utc)

    async def send(reason: str = "") -> None:
        try:
            turns, tool_calls = from_livekit(session.history)
            userdata = _userdata(session)
            body = {
                "platform": "livekit",
                "external_id": ctx.job.id,
                "agent": agent or _get(userdata, AGENT_KEY) or ctx.job.agent_name or None,
                "release": _get(userdata, RELEASE_KEY),
                "started_at": started.isoformat(),
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "turns": turns,
                "tool_calls": tool_calls,
                "metadata": {"room": ctx.room.name, "shutdown_reason": reason},
            }
            # stay inside the job's shutdown budget (LiveKit's graceful shutdown is 10s by default)
            await client.post_session(body, retries=1, timeout=3.0)
        except Exception as e:  # noqa: BLE001 - a failed post must not break shutdown
            log.error("sopops: couldn't send session %s: %s", getattr(ctx.job, "id", "?"), e)

    ctx.add_shutdown_callback(send)


def _userdata(session: Any) -> Any:
    try:
        return session.userdata
    except (ValueError, AttributeError):  # LiveKit raises ValueError when no userdata was set
        return None


def _get(userdata: Any, key: str) -> Any:
    return userdata.get(key) if isinstance(userdata, Mapping) else getattr(userdata, key, None)


def _remember(session: Any, ins: Instructions) -> None:
    userdata = _userdata(session)
    if userdata is None:
        session.userdata = {RELEASE_KEY: ins.release, AGENT_KEY: ins.agent}
    elif isinstance(userdata, dict):
        userdata[RELEASE_KEY], userdata[AGENT_KEY] = ins.release, ins.agent
    else:
        try:
            setattr(userdata, RELEASE_KEY, ins.release)
            setattr(userdata, AGENT_KEY, ins.agent)
        except AttributeError:
            log.warning("sopops: can't note the release on %s; add a %s field to it", type(userdata).__name__, RELEASE_KEY)


__all__ = ["attach", "from_livekit", "pinned_release", "record_session"]
