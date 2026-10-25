"""Optional hosted-product extensions.

The server has no dependency on a hosted product.  Installations that need one
can expose a ``sopops.extensions`` entry point whose callable receives the
FastAPI app, Store and Jobs objects at startup.  The callable can attach routes
and listeners without the public server importing a private package by name.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .jobs import Jobs
    from .store import Store

log = logging.getLogger("sopops.extensions")
ENTRY_POINTS = "sopops.extensions"
Register = Callable[["FastAPI", "Store", "Jobs"], None]


def load(app: FastAPI, store: Store, jobs: Jobs) -> None:
    """Load installed optional extensions.

    An extension failing to load must not stop a self-hosted deployment from
    starting.  Hosted deployments should alert on the logged exception.
    """
    for point in entry_points(group=ENTRY_POINTS):
        try:
            register: Register = point.load()
            register(app, store, jobs)
        except Exception:
            log.exception("couldn't load extension %s", point.name)
