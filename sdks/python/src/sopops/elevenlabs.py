"""ElevenLabs adapter: the compiled prompt as conversation overrides, and the conversation sent back to sopops.

    from sopops.elevenlabs import elevenlabs_session

    s = await elevenlabs_session(sopops, "agent_123")           # the ElevenLabs agent id
    s.initiation_data      # answer ElevenLabs's conversation initiation webhook with this, or send it first on a WebSocket
    s.overrides, s.dynamic_variables                            # or pass them to the ElevenLabs SDK:

    conversation = Conversation(
        elevenlabs, s.agent_id, requires_auth=True, audio_interface=DefaultAudioInterface(),
        config=ConversationInitiationData(conversation_config_override=s.overrides, dynamic_variables=s.dynamic_variables),
        **s.callbacks,                                          # with stream=True: transcripts go to sopops
    )
    conversation.start_session()
    conversation_id = await asyncio.to_thread(conversation.wait_for_session_end)
    await s.end(conversation_id=conversation_id)                # links the release; with stream=True, posts the conversation

The agent must allow overriding its system prompt (ElevenLabs: the agent's Security tab → Overrides), or ElevenLabs
rejects the session. This module doesn't import the `elevenlabs` package.

`link(conversation_id)` tells sopops which release the conversation ran on, so the session ElevenLabs's post-call
webhook sends links to it. With `stream=True`, `turn` and `tool_result` (and the `callbacks` that call them) keep the
conversation, stream it once `connect(conversation_id)` is called, and `end` posts it in full. They can be called from
the ElevenLabs SDK's own thread.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from .client import Instructions, SopOps
from .stream import SessionStream

log = logging.getLogger("sopops.elevenlabs")

Scalar = str | int | float | bool


async def elevenlabs_session(
    client: SopOps,
    agent: str,
    *,
    release: int | None = None,
    fallback: str | None = None,
    variables: Mapping[str, Any] | None = None,
    stream: bool = False,
    test: str | None = None,
    config: Mapping[str, Scalar] | None = None,
    tags: Sequence[str] | None = None,
    on_guidance: Callable[[Mapping[str, Any]], Any] | None = None,
) -> ElevenLabsSession:
    """Fetch the agent's instructions and build the overrides that carry them.

    `agent`: the ElevenLabs agent identifier, such as its agent id. `release`, `fallback` and `variables`: as on
    `SopOps.fetch_instructions`. `test`: an A/B test this conversation is part of (sent as the dynamic variable
    `sopops_test`). `config`: settings to report (`{"version": "v2"}`), sent as `sopops_<key>` dynamic variables;
    sopops reads the agent's LLM, voice and TTS model itself. `tags`: labels for this conversation (`["legacy"]`),
    sent as the dynamic variable `sopops_tags` and added to the agent's tags in sopops. `stream=True`: send the conversation to sopops as it
    happens, and in full at `end` (a token with the `ingest` scope is needed). `on_guidance`: not yet used: reserved
    for runtime guidance in a later version."""
    ins = await client.fetch_platform_instructions("elevenlabs", agent, release, fallback=fallback, variables=variables)
    platform, _, agent_id = (ins.platform_ref or "").partition(":")
    if platform != "elevenlabs" or not agent_id:
        raise ValueError(f"sopops agent '{ins.agent}' isn't an ElevenLabs agent")
    dynamic: dict[str, Scalar] = {"sopops_agent": ins.agent}
    if ins.release is not None:
        dynamic["sopops_release"] = ins.release
    if test:
        dynamic["sopops_test"] = test
    for k, v in (config or {}).items():
        dynamic[f"sopops_{k}"] = v
    if tags:
        dynamic["sopops_tags"] = ",".join(tags)
    return ElevenLabsSession(
        instructions=ins, agent_id=agent_id, overrides={"agent": {"prompt": {"prompt": ins.text}}}, dynamic_variables=dynamic,
        _client=client, _stream=stream, _tags={"test": test, "config": dict(config or {}), "tags": list(tags or [])}, _on_guidance=on_guidance,
        _loop=asyncio.get_running_loop(),
    )


@dataclass(eq=False)
class ElevenLabsSession:
    """What `elevenlabs_session` returns."""

    instructions: Instructions
    agent_id: str
    """The ElevenLabs agent id, from the agent's platform ref."""
    overrides: dict[str, Any]
    """`conversation_config_override`: the compiled prompt as `{"agent": {"prompt": {"prompt": ...}}}`."""
    dynamic_variables: dict[str, Scalar]
    """`sopops_agent`, `sopops_release`, and `sopops_test` / `sopops_tags` / `sopops_<key>` when given."""
    _client: SopOps = field(repr=False)
    _stream: bool = field(repr=False)
    _tags: dict[str, Any] = field(repr=False)
    _on_guidance: Callable[[Mapping[str, Any]], Any] | None = field(repr=False)
    _loop: asyncio.AbstractEventLoop = field(repr=False)
    _turns: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _tool_calls: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _conversation_id: str | None = field(default=None, repr=False)
    _live: SessionStream | None = field(default=None, repr=False)
    _ended: asyncio.Future[None] | None = field(default=None, repr=False)
    _tasks: set[asyncio.Future[Any]] = field(default_factory=set, repr=False)
    _started_at: str = field(default_factory=lambda: _now(), repr=False)

    @property
    def initiation_data(self) -> dict[str, Any]:
        """The `conversation_initiation_client_data` message: the answer to ElevenLabs's conversation initiation
        webhook (inbound phone calls), or the first message on a conversation WebSocket."""
        return {"type": "conversation_initiation_client_data", "conversation_config_override": self.overrides, "dynamic_variables": self.dynamic_variables}

    @property
    def callbacks(self) -> dict[str, Callable[..., None]]:
        """With `stream=True`, the ElevenLabs SDK's `Conversation` callbacks that keep the transcript
        (`callback_agent_response`, `callback_user_transcript`); empty otherwise. To add your own, call ours from it."""
        if not self._stream:
            return {}
        return {
            "callback_agent_response": lambda text: self.turn("agent", text),
            "callback_user_transcript": lambda text: self.turn("user", text),
        }

    async def link(self, conversation_id: str) -> None:
        """Report the conversation id to sopops so the post-call session links to this release. Never raises."""
        if self.instructions.release is None or not conversation_id:
            return
        try:
            await self._client.record_served("elevenlabs", conversation_id, self.instructions.agent, self.instructions.release)
        except Exception as e:  # noqa: BLE001 - linking is best effort
            log.warning("sopops: couldn't record the release for conversation %s: %s", conversation_id, e)

    def connect(self, conversation_id: str) -> None:
        """The conversation id is known: link the release and, with `stream=True`, start streaming what was said so
        far. Never raises; a repeat call is ignored."""
        self._call(lambda: self._connect(conversation_id))

    def turn(self, role: str, text: str) -> None:
        """With `stream=True`, keep a turn (`role` is `user` or `agent`) and stream it once connected."""
        self._call(lambda: self._turn({"role": role, "text": text, "at": _now()}))

    def tool_result(self, name: str, call_id: str | None = None, result: Any = None, is_error: bool = False) -> None:
        """With `stream=True`, keep a tool call and its result and stream it once connected."""
        def add() -> None:
            at = _now()
            call: dict[str, Any] = {"name": name, "is_error": is_error, "at": at}
            if call_id is not None:
                call["call_id"] = call_id
            if result is not None:
                call["result"] = result
            self._turn({"role": "tool", "text": name, "name": name, "at": at})
            if self._stream and self._ended is None:
                self._tool_calls.append(call)
                if self._live is not None:
                    self._live.tool_call(call)
        self._call(add)

    async def end(self, reason: str = "", *, conversation_id: str | None = None) -> None:
        """Stop streaming and post the full conversation (with `stream=True`). `conversation_id` connects first if
        `connect` wasn't called. Never raises; repeat calls wait for the first."""
        if conversation_id:
            self._connect(conversation_id)
        if self._ended is None:
            self._ended = asyncio.ensure_future(self._post(reason))
        await asyncio.shield(self._ended)

    # --- internals -----------------------------------------------------------------------------------

    def _call(self, fn: Callable[[], None]) -> None:
        """Run on the session's event loop: the ElevenLabs SDK calls back from its own thread."""
        try:
            if _running_loop() is self._loop:
                fn()
            else:
                self._loop.call_soon_threadsafe(self._safely, fn)
        except Exception as e:  # noqa: BLE001 - never raise into the conversation
            log.warning("sopops: couldn't keep a conversation event: %s", e)

    @staticmethod
    def _safely(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log.warning("sopops: couldn't keep a conversation event: %s", e)

    def _connect(self, conversation_id: str) -> None:
        if self._conversation_id or not conversation_id:
            return
        self._conversation_id = conversation_id
        task = asyncio.ensure_future(self.link(conversation_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if not self._stream:
            return
        self._live = self._client.stream(
            {"platform": "elevenlabs", "external_id": conversation_id, "channel": "voice", "agent": self.instructions.agent,
             "release": self.instructions.release, "started_at": self._started_at, **self._tags},
            on_guidance=self._on_guidance,
        )
        for t in self._turns:  # anything said before the id was known
            self._live.turn(t)
        for c in self._tool_calls:
            self._live.tool_call(c)

    def _turn(self, turn: dict[str, Any]) -> None:
        if not self._stream or self._ended is not None:
            return
        self._turns.append(turn)
        if self._live is not None:
            self._live.turn(turn)

    async def _post(self, reason: str) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if not self._stream or self._live is None or not self._conversation_id:
            return  # never connected: nothing to key the session by
        await self._live.aclose()
        try:
            await self._client.post_session({
                "platform": "elevenlabs", "external_id": self._conversation_id, "channel": "voice", "agent": self.instructions.agent,
                "release": self.instructions.release, "started_at": self._started_at, "ended_at": _now(), "turns": self._turns,
                "tool_calls": self._tool_calls, "metadata": {"disconnect_reason": reason}, **self._tags,
            })
        except Exception as e:  # noqa: BLE001 - a failed post must not reach the caller
            log.warning("sopops: couldn't send conversation %s: %s", self._conversation_id, e)


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["ElevenLabsSession", "elevenlabs_session"]
