"""LiveKit Agents adapter (`pip install "sopops[livekit]"`).

    from sopops.livekit import attach

    async def entrypoint(ctx: JobContext):
        await ctx.connect()
        session = AgentSession(...)
        ins = await attach(ctx, session, agent="tonys-pizza")
        await session.start(room=ctx.room, agent=Agent(instructions=ins.text))

`attach` fetches the instructions (honoring a release pinned in job or room metadata), keeps the release in
`session.userdata`, and at shutdown posts the session (`session.history` with timestamps and tool calls) to
sopops, with what it ran with: the session's LLM, STT and TTS models and voice (`agent_config`), so sopops can
compare an agent's versions with no extra setup. `test=` names an A/B test and `config=` adds settings. With `stream=True` it also sends each turn and tool call as it happens. Nothing here imports livekit at
import time; the objects are used through their documented attributes and events.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Mapping

from .client import Instructions, SopOps, release_pin
from .stream import SessionStream

if TYPE_CHECKING:  # only for type checkers: the adapter needs no livekit import at runtime
    from livekit.agents import AgentSession, JobContext

log = logging.getLogger("sopops.livekit")

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
            turn = _message_turn(item.get("role"), item.get("content", []), at, item.get("interrupted", False))
            if not _repeats(turns[-1] if turns else None, turn):
                turns.append(turn)
        elif kind == "function_call":
            name, call_id = item.get("name"), item.get("call_id")
            turns.append(_tool_turn(name, item.get("arguments"), at))
            tools[call_id] = {"name": name, "arguments": item.get("arguments"), "call_id": call_id, "at": at}
        elif kind == "function_call_output" and item.get("call_id") in tools:
            tools[item["call_id"]].update(result=item.get("output"), is_error=bool(item.get("is_error", False)))
    return turns, list(tools.values())


def _message_turn(role: Any, content: Any, at: Any, interrupted: Any) -> dict[str, Any]:
    text = " ".join(c for c in content or [] if isinstance(c, str)).strip()
    return {"role": role, "text": text, "at": at, "interrupted": bool(interrupted)}


def _tool_turn(name: Any, arguments: Any, at: Any) -> dict[str, Any]:
    return {"role": "tool", "text": f"{name}({arguments if arguments is not None else ''})", "name": name, "at": at}


def _repeats(prev: Mapping[str, Any] | None, turn: Mapping[str, Any]) -> bool:
    """LiveKit can add a user transcript twice."""
    return prev is not None and prev["role"] == turn["role"] and prev["text"] == turn["text"] and prev.get("name") is None


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
    client: SopOps | None = None,
    release: int | None = None,
    fallback: str | None = None,
    stream: bool = False,
    test: str | None = None,
    config: Mapping[str, Any] | None = None,
    variables: Mapping[str, Any] | None = None,
    on_guidance: Callable[[Mapping[str, Any]], Any] | None = None,
) -> Instructions:
    """Fetch the agent's instructions, note the release in `session.userdata`, and post the session at shutdown.

    `agent`: the LiveKit agent identifier, such as its name or phone number; default: the job's agent name.
    `release` pins one; default: a pin in job or room metadata, else the live release. `fallback`: text to use if
    sopops is down and nothing is cached (see `SopOps.fetch_instructions`). Call it after `ctx.connect()` for room
    metadata.

    `stream=True` also sends each turn and tool call while the call runs (see `record_session`).
    `test`: an A/B test this call is part of. `config`: settings to report beyond the models `agent_config` reads
    (`{"version": "v2", "turn_detection": "eou"}`); they win over what's read.
    `variables`: values for `{{placeholders}}` only this call knows, like the time or the caller. They're filled
    here, in your process, so they never reach sopops. A placeholder you pass no value for is left as it is and
    listed in `ins.unfilled`, so you can log one that shouldn't be there.
    `on_guidance`: not yet used: reserved for runtime guidance in a later version."""
    client = client or SopOps()
    agent = agent or ctx.job.agent_name
    if not agent:
        raise ValueError("pass agent= when the LiveKit job has no agent name")
    pin = release if release is not None else pinned_release(ctx)
    ins = await client.fetch_platform_instructions("livekit", agent, pin, fallback=fallback, variables=variables)
    _remember(session, ins)
    record_session(ctx, session, client=client, agent=ins.agent, stream=stream, test=test, config=config, on_guidance=on_guidance)
    return ins


def record_session(
    ctx: JobContext,
    session: AgentSession[Any],
    *,
    client: SopOps | None = None,
    agent: str | None = None,
    stream: bool = False,
    test: str | None = None,
    config: Mapping[str, Any] | None = None,
    on_guidance: Callable[[Mapping[str, Any]], Any] | None = None,
) -> None:
    """Post `session.history` to sopops when the job shuts down, keyed by the job id (rooms get reused), with the
    release from `session.userdata`. Errors are logged, never raised into shutdown.

    `stream=True` also opens the session now and sends each turn and tool call as it happens, in batches from a
    background task (see `SessionStream`); it never blocks or raises into the agent. The full post at shutdown
    still happens and replaces what was streamed. `test` and `config`: see `attach`. `on_guidance`: not yet used: reserved for runtime guidance in
    a later version."""
    client = client or SopOps()
    started = datetime.now(timezone.utc)
    live = _stream(ctx, session, client, agent, started, on_guidance, test, config) if stream else None

    async def send(reason: str = "") -> None:
        if live is not None:
            await live.aclose()
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
                "test": test,
                "config": {**agent_config(session), **dict(config or {})},
            }
            # stay inside the job's shutdown budget (LiveKit's graceful shutdown is 10s by default)
            await client.post_session(body, retries=1, timeout=3.0)
        except Exception as e:  # noqa: BLE001 - a failed post must not break shutdown
            log.error("sopops: couldn't send session %s: %s", getattr(ctx.job, "id", "?"), e)

    ctx.add_shutdown_callback(send)


def _stream(
    ctx: JobContext, session: AgentSession[Any], client: SopOps, agent: str | None, started: datetime, on_guidance: Callable[[Mapping[str, Any]], Any] | None,
    test: str | None = None, config: Mapping[str, Any] | None = None,
) -> SessionStream:
    """Open a SessionStream for the job and feed it from the session's `conversation_item_added` and
    `function_tools_executed` events."""
    userdata = _userdata(session)
    live = SessionStream(
        client,
        {
            "platform": "livekit",
            "external_id": ctx.job.id,
            "channel": "voice",
            "agent": agent or _get(userdata, AGENT_KEY) or ctx.job.agent_name or None,
            "release": _get(userdata, RELEASE_KEY),
            "started_at": started.isoformat(),
            "metadata": {"room": ctx.room.name},
            "test": test,
            "config": {**agent_config(session), **dict(config or {})},
        },
        on_guidance=on_guidance,
    )
    last: list[dict[str, Any]] = []  # the last turn sent, to drop repeats like from_livekit does

    def on_item(event: Any) -> None:
        try:
            item = event.item
            if getattr(item, "type", "message") != "message":
                return
            turn = _message_turn(item.role, item.content, getattr(item, "created_at", None), getattr(item, "interrupted", False))
            if not _repeats(last[0] if last else None, turn):
                last[:] = [turn]
                live.turn(turn)
        except Exception as e:  # noqa: BLE001 - never raise into the agent
            log.warning("sopops: couldn't stream a conversation item: %s", e)

    def on_tools(event: Any) -> None:
        try:
            outputs = list(getattr(event, "function_call_outputs", None) or [])
            for i, call in enumerate(event.function_calls):
                out = outputs[i] if i < len(outputs) else None
                at = getattr(call, "created_at", None)
                turn = _tool_turn(call.name, call.arguments, at)
                last[:] = [turn]
                live.turn(turn)
                tool_call = {"name": call.name, "arguments": call.arguments, "call_id": call.call_id, "at": at}
                if out is not None:
                    tool_call.update(result=out.output, is_error=bool(getattr(out, "is_error", False)))
                live.tool_call(tool_call)
        except Exception as e:  # noqa: BLE001 - never raise into the agent
            log.warning("sopops: couldn't stream a tool call: %s", e)

    session.on("conversation_item_added", on_item)
    session.on("function_tools_executed", on_tools)
    live.start()
    return live


def agent_config(session: Any) -> dict[str, Any]:
    """What an `AgentSession` runs with: `llm`, `stt`, `tts` as `<provider>/<model>` (LiveKit Inference models are
    already written that way), and `voice` when the TTS keeps one. Whatever a plugin doesn't say is left out."""
    out: dict[str, Any] = {}
    for role in ("llm", "stt", "tts"):
        try:
            part = getattr(session, role, None)
        except Exception:  # noqa: BLE001 - a property that can't answer leaves the setting out
            part = None
        if part is None:
            continue
        name = _model_name(getattr(part, "provider", None), getattr(part, "model", None))
        if name:
            out[role] = name
        if role == "tts":
            opts = getattr(part, "_opts", None)
            voice = getattr(part, "voice", None) or getattr(opts, "voice", None) or getattr(opts, "voice_id", None)
            if isinstance(voice, str) and voice:
                out["voice"] = voice
    return out


def _model_name(provider: Any, model: Any) -> str | None:
    model = model if isinstance(model, str) and model.lower() not in ("", "unknown") else None
    provider = provider if isinstance(provider, str) and provider.lower() not in ("", "unknown", "livekit") else None
    if not model:
        return None
    return model if "/" in model or not provider else f"{_provider_name(provider)}/{model}"


def _provider_name(provider: str) -> str:
    """Some plugins report their API host as the provider (`api.anthropic.com`): keep the company's name."""
    host = provider.lower().split("://")[-1].split("/")[0]
    labels = [p for p in host.split(".") if p]
    return labels[-2] if len(labels) >= 2 else host


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


__all__ = ["agent_config", "attach", "from_livekit", "pinned_release", "record_session"]
