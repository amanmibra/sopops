"""sopops: versioned sopc workspaces over HTTP, and the instructions each agent gets at call start.

`create_app` builds the FastAPI app and mounts the routers in `sopops_server.api`. Its OpenAPI spec (/openapi.json) is what
SDKs are generated from. Set DATABASE_URL for the database (default sqlite:///sopops.db) and SOPC_BIN for the sopc
binary (default: `sopc` on PATH). Sign-in with GitHub or Google is on once a login provider is configured
(`sopops_server.auth`); `sopops_server.auth.access` says how every route is authenticated.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute

from . import __version__
from .api import (
    ai,
    auth,
    capture,
    config,
    errors,
    files,
    github,
    insights,
    lineage,
    onboarding,
    patterns,
    provider_webhooks,
    releases,
    repo,
    review,
    runtime,
    serving,
    sessions,
    stateless,
    suggestions,
    support,
    tokens,
    ui,
    usage,
)
from .api.deps import OPEN
from .auth import access, providers
from .auth.service import Accounts
from .db import database_url
from .events import PgListener
from .jobs import Jobs
from .onboarding.service import Onboarding, ai_from_env
from .sopc import Sopc
from .store import Store


def _operation_id(route: APIRoute) -> str:
    """Operation ids (and so the generated client's function names) are the route names: `list_workspaces`, `get_agent`."""
    return route.name


def create_app(store: Store, jobs: Jobs | None = None) -> FastAPI:
    jobs = jobs or Jobs(store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # On Postgres, session events from every process (a Hatchet worker too) arrive by LISTEN/NOTIFY.
        listener = None
        if store.sessions.postgres:
            url = store.engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
            listener = PgListener(url, store.sessions.broker)
            listener.start()
            app.state.pg_listener = listener
        yield
        if listener:
            listener.stop()

    app = FastAPI(
        title="sopops",
        version=__version__,
        description="Versioned sopc workspaces: edit files, release them, and serve each agent its full instructions at call start. "
        "Sessions (voice calls and text chats) come in through the ingest endpoints.",
        generate_unique_id_function=_operation_id,
        lifespan=lifespan,
        dependencies=[Depends(access.guard)],  # every route: its kind in access.ROUTES, and who the request acts as
    )
    providers.load_plugins()
    app.state.store = store
    app.state.accounts = Accounts(store)
    app.state.jobs = jobs
    # Jobs(ai=...) reaches imports too
    app.state.onboarding = Onboarding(store, ai=lambda ws=None: jobs.ai if jobs.ai is not None else ai_from_env(store, ws))
    errors.register(app)
    rt = runtime.register(app, store)  # push to platforms, their call-start webhooks, runtime token

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"ok": True}

    for r in (
        auth.router(app.state.accounts),
        stateless.router(store),
        files.router(store),
        config.router(store),
        releases.router(store, rt),
        serving.router(store, rt),
        sessions.router(store, jobs),
        capture.router(store, jobs),
        tokens.router(store),
        patterns.router(store, jobs),
        review.router(store, jobs),
        suggestions.router(store, jobs),
        insights.router(store),
        lineage.router(store),
        usage.router(store),
        ai.router(store),
        repo.router(store),
        github.router(store),
        provider_webhooks.router(store),
        support.router(store),
    ):
        app.include_router(r)
    app.include_router(onboarding.router(store, app.state.onboarding), dependencies=OPEN)
    app.include_router(ui.router())
    return app


def app_from_env() -> FastAPI:
    return create_app(Store(database_url(), Sopc()))
