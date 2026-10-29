"""Drafts and releases: publish, list, activate (roll back). Agents set to push get each new release on their platform."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from ..runtime.service import Runtime
from ..schemas import (
    DraftResponse,
    PublishRequest,
    PublishResponse,
    ReleaseDetail,
    ReleaseRequest,
    ReleaseResponse,
    ReleaseSummary,
    WorkspaceOut,
)
from ..sop import without_build
from ..store import Store
from .deps import INVALID, OPEN
from .runtime import push_on_release
from .serving import released


def router(store: Store, runtime: Runtime) -> APIRouter:
    r = APIRouter()

    @r.get("/v1/workspaces/{workspace}/draft", response_model=DraftResponse, dependencies=OPEN, tags=["releases"])
    def draft(workspace: str) -> dict:
        """Unpublished changes: head compiled and compared with the current release."""
        return store.draft(workspace)

    @r.post("/v1/workspaces/{workspace}/releases", response_model=ReleaseResponse, responses=INVALID, dependencies=OPEN, tags=["releases"])
    def create_release(workspace: str, background: BackgroundTasks, req: ReleaseRequest | None = None) -> dict:
        """Release head and serve it. Agents set to push get it on their platform, after the response."""
        req = req or ReleaseRequest()
        release, created = store.release(workspace, req.author, req.note)
        if created:
            push_on_release(runtime, background, workspace, release["number"])
        return {"release": release, "created": created}

    @r.get("/v1/workspaces/{workspace}/releases", response_model=list[ReleaseSummary], dependencies=OPEN, tags=["releases"])
    def list_releases(workspace: str) -> list[dict]:
        return store.releases(workspace)

    @r.get("/v1/workspaces/{workspace}/releases/{number}", response_model=ReleaseDetail, dependencies=OPEN, tags=["releases"])
    def get_release(workspace: str, number: int) -> dict:
        """A release with every agent's instructions. Doesn't log a fetch."""
        rel = store.get_release(workspace, number)
        return {**rel, "agents": {a: released(b) for a, b in rel["build"]["agents"].items()}}

    @r.post("/v1/workspaces/{workspace}/releases/{number}/activate", response_model=WorkspaceOut, dependencies=OPEN, tags=["releases"])
    def activate(workspace: str, number: int, background: BackgroundTasks) -> dict:
        """Serve this release, e.g. to roll back. Agents set to push get it on their platform, after the response."""
        out = store.activate(workspace, number)
        push_on_release(runtime, background, workspace, number)
        return out

    @r.post("/v1/workspaces/{workspace}/publish", response_model=PublishResponse, responses=INVALID, dependencies=OPEN, tags=["releases"])
    def publish(workspace: str, req: PublishRequest, background: BackgroundTasks) -> dict:
        """Replace head with these files and release it: the CI path for git workspaces."""
        release, created, changed = store.publish(workspace, without_build(req.files), req.author, req.note)
        if created:
            push_on_release(runtime, background, workspace, release["number"])
        return {"release": release, "created": created, "changed": changed}

    return r
