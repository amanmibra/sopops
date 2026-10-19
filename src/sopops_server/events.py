"""Session events (created, updated) for live views.

`Broker` is an in-process pub/sub keyed by workspace. On SQLite the store publishes to it directly after commit. On
Postgres the store sends `NOTIFY sopops_sessions` inside the write's transaction instead, and `PgListener` (a thread
in the API process) LISTENs and republishes, so changes made by another process, such as a Hatchet worker, reach the
API's subscribers too.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from typing import Callable

log = logging.getLogger("sopops.events")

CHANNEL = "sopops_sessions"
Event = dict
Callback = Callable[[Event], None]


class Broker:
    def __init__(self) -> None:
        self._subs: dict[str, set[Callback]] = defaultdict(set)
        self._lock = threading.Lock()

    def subscribe(self, workspace: str, callback: Callback) -> Callable[[], None]:
        """Call `callback(event)` for each event in the workspace, from whichever thread publishes. Returns an unsubscribe."""
        with self._lock:
            self._subs[workspace].add(callback)

        def unsubscribe() -> None:
            with self._lock:
                self._subs[workspace].discard(callback)

        return unsubscribe

    def publish(self, event: Event) -> None:
        with self._lock:
            subs = list(self._subs.get(event["workspace"], ()))
        for cb in subs:
            try:
                cb(event)
            except Exception:  # one broken subscriber mustn't stop the others
                log.exception("session event subscriber failed")


class PgListener:
    """LISTEN on Postgres and republish each notification to the broker. Reconnects if the connection drops."""

    def __init__(self, url: str, broker: Broker) -> None:
        self.url, self.broker = url, broker
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sopops-pg-listener", daemon=True)
        self.ready = threading.Event()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        import psycopg

        while not self._stop.is_set():
            try:
                with psycopg.connect(self.url, autocommit=True) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    self.ready.set()
                    while not self._stop.is_set():
                        for n in conn.notifies(timeout=1.0):
                            try:
                                self.broker.publish(json.loads(n.payload))
                            except ValueError:
                                log.warning("bad %s payload: %r", CHANNEL, n.payload)
            except Exception:
                log.exception("Postgres LISTEN failed; retrying in 2s")
                self.ready.clear()
                self._stop.wait(2.0)
