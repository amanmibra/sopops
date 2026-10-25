"""Send a session's turns and tool calls to sopops while the call runs (optional).

    stream = SessionStream(sopops, {"platform": "api", "external_id": call_id, "agent": "tonys-pizza", "release": 3})
    stream.start()                                       # from inside the running event loop
    stream.turn({"role": "user", "text": "Delivery please.", "at": time.time()})
    stream.tool_call({"name": "check_delivery_zone", "arguments": {...}, "result": {...}})
    ...
    await stream.aclose()                                # sends what's queued, then stops
    await sopops.post_session(full_session)              # the full session is still the source of truth

A background task opens the session and sends events in batches. `turn` and `tool_call` never block and never
raise: when the queue is full, or sopops fails, events are logged and dropped, and the full session posted at the
end fills the gap.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Literal, Mapping

from .client import SopOps

log = logging.getLogger("sopops.stream")

EventType = Literal["turn", "tool_call"]
OPEN_FIELDS = ("platform", "external_id", "channel", "agent", "release", "version_ref", "started_at", "metadata", "test", "config")


class SessionStream:
    """Streams one session's events to sopops from a background task.

    `session` holds the fields `SopOps.open_session` takes (`platform` and `external_id` are required).
    Sending starts on the first event queued inside a running event loop, or on `start()`.
    Events are sent at most every `flush_interval` seconds, at most `max_batch` per request; at most `max_queue`
    wait to be sent, and the rest are dropped.
    """

    def __init__(
        self,
        client: SopOps,
        session: Mapping[str, Any],
        *,
        flush_interval: float = 0.5,
        max_batch: int = 50,
        max_queue: int = 1000,
        on_guidance: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self.client = client
        self.session = {k: session[k] for k in OPEN_FIELDS if session.get(k) is not None}
        self.platform: str = session["platform"]
        self.external_id: str = session["external_id"]
        self.flush_interval = flush_interval
        self.max_batch = max_batch
        self.on_guidance = on_guidance
        """Not yet used: reserved for runtime guidance in a later version."""
        self.sent = 0
        self.dropped = 0
        self._seq = 0
        self._opened = False
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(max_queue)
        self._held: list[dict[str, Any]] = []  # taken off the queue, waiting for the batch to fill
        self._runner: asyncio.Task[None] | None = None
        self._inflight: asyncio.Task[None] | None = None
        self._closed = False

    def start(self) -> None:
        """Start the background sender. Call from inside the running event loop. Optional: queueing an event inside
        a running loop starts it too."""
        if self._runner is None and not self._closed:
            self._runner = asyncio.get_running_loop().create_task(self._run(), name=f"sopops-stream-{self.external_id}")

    def turn(self, turn: Mapping[str, Any]) -> None:
        """Queue a turn (`{role, text, at | offset_s, ...}`, as in the canonical format)."""
        self._add("turn", turn)

    def tool_call(self, call: Mapping[str, Any]) -> None:
        """Queue a tool call (`{name, arguments, result, is_error, call_id, at | offset_s}`)."""
        self._add("tool_call", call)

    async def aclose(self, timeout: float = 1.0) -> None:
        """Stop the sender after sending what's queued, giving up after `timeout` seconds. Never raises."""
        self._closed = True
        try:
            await asyncio.wait_for(self._finish(), timeout)
        except Exception as e:  # noqa: BLE001 - closing must not break the caller's shutdown
            log.warning("sopops: stopped streaming session %s with events unsent: %s", self.external_id, str(e) or type(e).__name__)

    def _add(self, kind: EventType, payload: Mapping[str, Any]) -> None:
        if self._closed:
            return
        event = {"seq": self._seq, "type": kind, kind: dict(payload)}
        self._seq += 1
        if self._runner is None:
            try:
                self.start()
            except RuntimeError:  # no running loop yet: start() later sends what's queued
                pass
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped == 1:
                log.warning("sopops: the event queue for session %s is full; dropping events", self.external_id)

    def _take(self) -> list[dict[str, Any]]:
        batch, self._held = self._held, []
        while len(batch) < self.max_batch and not self._queue.empty():
            batch.append(self._queue.get_nowait())
        return batch

    async def _run(self) -> None:
        while True:
            self._held.append(await self._queue.get())
            if self._queue.qsize() < self.max_batch - 1:
                await asyncio.sleep(self.flush_interval)  # let the batch fill
            self._inflight = asyncio.ensure_future(self._send(self._take()))
            # shielded, so stopping the runner lets a batch already on its way finish
            await asyncio.shield(self._inflight)

    async def _finish(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            try:
                await self._runner
            except asyncio.CancelledError:
                pass
        if self._inflight is not None:
            await self._inflight
        while batch := self._take():
            await self._send(batch)

    async def _send(self, batch: list[dict[str, Any]]) -> None:
        try:
            if not self._opened:
                await self.client.open_session(self.session)
                self._opened = True
            await self.client.append_events(self.platform, self.external_id, batch)
            self.sent += len(batch)
        except Exception as e:  # noqa: BLE001 - streaming is best effort and must never reach the agent
            self.dropped += len(batch)
            log.warning("sopops: dropped %d events for session %s: %s", len(batch), self.external_id, str(e) or type(e).__name__)


__all__ = ["SessionStream"]
