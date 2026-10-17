"""Where pipeline runs go: Hatchet when it's configured (HATCHET_CLIENT_TOKEN is set), else inline.

Inline means a FastAPI background task after the response (the API) or right away (the import CLI), in this process.
That's how local development and the tests run, with no Hatchet at all.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from ..pipeline import SessionRef, process_session

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

    from ..store import Store

log = logging.getLogger("sopops.jobs")


def hatchet_configured() -> bool:
    return bool(os.environ.get("HATCHET_CLIENT_TOKEN"))


class Jobs:
    def __init__(self, store: Store, use_hatchet: bool | None = None):
        self.store = store
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

    def run_inline(self, ref: SessionRef) -> None:
        try:
            process_session(self.store, ref)
        except Exception:  # the session is marked failed with the reason; nothing else to do here
            log.exception("process_session failed for %s", ref.session_id)
