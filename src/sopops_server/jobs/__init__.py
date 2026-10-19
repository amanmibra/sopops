"""Where pipeline runs, suggestion generation and calibration go: Hatchet when it's configured (HATCHET_CLIENT_TOKEN is set), else
inline.

Inline means a FastAPI background task after the response (the API) or right away (the import CLI), in this process.
That's how local development and the tests run, with no Hatchet at all.

The live recorders (a LiveKit room, an ElevenLabs monitor socket; `sopops_server.capture`) run for as long as the call: on
Hatchet in the worker, else as tasks on this process's event loop, one per room or conversation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import TYPE_CHECKING, Any, Coroutine

from ..capture import LiveConversationRef, LiveRoomRef
from ..capture.elevenlabs import watch_conversation
from ..capture.livekit import watch_room
from ..pipeline.process import process_session, session_event
from ..pipeline.types import CalibrateRef, SessionRef, SuggestRef

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

    from ..ai.lm import AI
    from ..store import Store

log = logging.getLogger("sopops.jobs")


def hatchet_configured() -> bool:
    return bool(os.environ.get("HATCHET_CLIENT_TOKEN"))


class Jobs:
    def __init__(self, store: Store, use_hatchet: bool | None = None, ai: AI | None = None):
        """`ai`: the models for inline runs (tests pass a fake); default: `sopops_server.ai.lm.Models` from the environment."""
        self.store = store
        self.ai = ai
        self.use_hatchet = hatchet_configured() if use_hatchet is None else use_hatchet
        self._live: dict[tuple[str, str, str], tuple[Any, asyncio.Event | None]] = {}  # inline recorders, by call

    @property
    def mode(self) -> str:
        return "hatchet" if self.use_hatchet else "inline"

    def process_session(self, ref: SessionRef, background: BackgroundTasks | None = None) -> str:
        """Queue `process_session` for the session. Returns where it went: hatchet, background or inline."""
        if self.use_hatchet:
            try:
                from .hatchet import trigger_process_session

                trigger_process_session(ref)
                return "hatchet"
            except Exception:  # Hatchet down: still process it rather than leave it waiting
                log.exception("couldn't queue process_session on Hatchet; running it here")
        if background is not None:
            background.add_task(self.run_inline, ref)
            return "background"
        self.run_inline(ref)
        return "inline"

    def suggest(self, ref: SuggestRef, background: BackgroundTasks | None = None) -> str:
        """Queue a suggestion's generation (`Suggestions.generate`). Returns where it went, like `process_session`."""
        if self.use_hatchet:
            try:
                from .hatchet import trigger_suggest

                trigger_suggest(ref)
                return "hatchet"
            except Exception:
                log.exception("couldn't queue a suggestion on Hatchet; running it here")
        if background is not None:
            background.add_task(self.suggest_inline, ref)
            return "background"
        self.suggest_inline(ref)
        return "inline"

    def suggest_inline(self, ref: SuggestRef) -> None:
        self.store.suggestions.generate(ref.workspace_id, ref.suggestion_id, self.ai)  # failures are stored on the suggestion

    def calibrate(self, ref: CalibrateRef) -> dict:
        """Fit the workspace's check thresholds to its labels (`Calibrate.run`) and wait for the report: on Hatchet when
        it's configured, else here."""
        if self.use_hatchet:
            try:
                from .hatchet import run_calibrate

                return run_calibrate(ref)
            except Exception:
                log.exception("couldn't run calibration on Hatchet; running it here")
        return self.store.calibrate.run(ref.workspace_id, ref.procedure_id, ref.dry_run, self.ai)

    def session_event(self, ref: SessionRef, events: list[dict]) -> None:
        """The pipeline's hook for events of an open session, here and now (it does nothing yet)."""
        session_event(self.store, ref, events)

    def run_inline(self, ref: SessionRef) -> None:
        try:
            process_session(self.store, ref, self.ai)
        except Exception:  # the session is marked failed with the reason; nothing else to do here
            log.exception("process_session failed for %s", ref.session_id)

    # --- live recorders (sopops_server.capture) ---------------------------------------------------------

    def watch_livekit_room(self, ref: LiveRoomRef) -> str:
        """Record a LiveKit room until its call ends (`capture.livekit.watch_room`), once per room. Returns where:
        hatchet, inline, or already (a recorder is in the room)."""
        if self.use_hatchet:
            try:
                from .hatchet import trigger_livekit_room

                trigger_livekit_room(ref)
                return "hatchet"
            except Exception:
                log.exception("couldn't queue the LiveKit recorder on Hatchet; running it here")
        key = ("livekit", ref.workspace_id, ref.room)
        if self._running(key):
            return "already"
        stop = asyncio.Event()
        self._live[key] = (self._spawn(watch_room(self.store, ref, self.process_session, stop=stop), key), stop)
        return "inline"

    def end_livekit_room(self, workspace: str, room: str) -> bool:
        """The room finished: stop its recorder here, if there is one (it closes the session). On Hatchet the
        recorder sees the room close by itself."""
        live = self._live.get(("livekit", workspace, room))
        if live is None or live[1] is None or _done(live[0]):
            return False
        live[1].set()
        return True

    def watch_elevenlabs(self, ref: LiveConversationRef) -> str:
        """Follow an ElevenLabs conversation on its monitor socket (`capture.elevenlabs.watch_conversation`)."""
        if self.use_hatchet:
            try:
                from .hatchet import trigger_elevenlabs_monitor

                trigger_elevenlabs_monitor(ref)
                return "hatchet"
            except Exception:
                log.exception("couldn't queue the ElevenLabs monitor on Hatchet; running it here")
        key = ("elevenlabs", ref.workspace_id, ref.conversation_id)
        if self._running(key):
            return "already"
        self._live[key] = (self._spawn(watch_conversation(self.store, ref, self.process_session), key), None)
        return "inline"

    def _running(self, key: tuple[str, str, str]) -> bool:
        live = self._live.get(key)
        return live is not None and not _done(live[0])

    def _spawn(self, work: Coroutine[Any, Any, dict], key: tuple[str, str, str]) -> Any:
        """On this process's event loop (a webhook handler's), else on a thread of its own."""

        async def run() -> None:
            try:
                await work
            except Exception:
                log.exception("live recorder %s failed", key[0])
            finally:
                self._live.pop(key, None)

        try:
            return asyncio.get_running_loop().create_task(run(), name=f"sopops-{key[0]}-{key[2]}")
        except RuntimeError:
            thread = threading.Thread(target=asyncio.run, args=(run(),), name=f"sopops-{key[0]}-{key[2]}", daemon=True)
            thread.start()
            return thread


def _done(handle: Any) -> bool:
    return handle.done() if isinstance(handle, asyncio.Task) else not handle.is_alive()
