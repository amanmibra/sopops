"""Where pipeline runs and suggestion generation go: Hatchet when it's configured (HATCHET_CLIENT_TOKEN is set), else
inline.

Inline means a FastAPI background task after the response (the API) or right away (the import CLI), in this process.
That's how local development and the tests run, with no Hatchet at all.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from ..pipeline.process import process_session, session_event
from ..pipeline.types import SessionRef, SuggestRef

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

    from ..ai.lm import AI
    from ..store import Store

log = logging.getLogger("sopops.jobs")


def hatchet_configured() -> bool:
    return bool(os.environ.get("HATCHET_CLIENT_TOKEN"))


class Jobs:
    def __init__(self, store: Store, use_hatchet: bool | None = None, ai: AI | None = None):
        """`ai`: the models for inline runs (tests pass a fake); default: `sopops.ai.lm.Models` from the environment."""
        self.store = store
        self.ai = ai
        self.use_hatchet = hatchet_configured() if use_hatchet is None else use_hatchet

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

    def session_event(self, ref: SessionRef, events: list[dict]) -> None:
        """The pipeline's hook for events of an open session, here and now (it does nothing yet)."""
        session_event(self.store, ref, events)

    def run_inline(self, ref: SessionRef) -> None:
        try:
            process_session(self.store, ref, self.ai)
        except Exception:  # the session is marked failed with the reason; nothing else to do here
            log.exception("process_session failed for %s", ref.session_id)
