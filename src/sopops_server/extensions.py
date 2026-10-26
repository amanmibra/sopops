"""Optional hosted-product extensions.

The server has no dependency on a hosted product.  Installations that need one
can expose a ``sopops.extensions`` entry point whose callable receives the
FastAPI app, Store and Jobs objects at startup.  The callable can attach routes
and listeners (session events, and model calls through `sopops_server.ai.tracing`)
without the public server importing a private package by name.

The Hatchet worker (`sopops worker`), where checks run when Hatchet is on, loads them too (`load_worker`): there the
app is a bare FastAPI that serves nothing, with `app.state.sopops_worker` set, so an extension can skip its routes and
keep what matters in a worker, like its model-call listeners.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Callable

from fastapi import FastAPI

from .jobs import Jobs

if TYPE_CHECKING:
    from .store import Store

log = logging.getLogger("sopops.extensions")
ENTRY_POINTS = "sopops.extensions"
Register = Callable[["FastAPI", "Store", "Jobs"], None]


def load(app: FastAPI, store: Store, jobs: Jobs) -> None:
    """Load installed optional extensions.

    An extension failing to load must not stop a self-hosted deployment from
    starting.  Hosted deployments should alert on the logged exception.
    """
    app.state.sopops_worker = getattr(app.state, "sopops_worker", False)
    for point in entry_points(group=ENTRY_POINTS):
        try:
            register: Register = point.load()
            register(app, store, jobs)
        except Exception:
            log.exception("couldn't load extension %s", point.name)


def load_worker(store: Store) -> None:
    """Load the extensions in the Hatchet worker, which has no app of its own."""
    app = FastAPI()
    app.state.sopops_worker = True
    load(app, store, Jobs(store, use_hatchet=True))
