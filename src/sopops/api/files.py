"""Workspaces and their files: head, edits, history."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..auth import actor
from ..schemas import (
    EditRequest,
    FileOut,
    FilesResponse,
    ReplaceRequest,
    WorkspaceOut,
    WriteResponse,
)
from ..sopc import without_build
from ..store import Store
from .deps import INVALID, OPEN


def router(store: Store) -> APIRouter:
    r = APIRouter()

    @r.get("/v1/workspaces", response_model=list[WorkspaceOut], dependencies=OPEN, tags=["workspaces"])
    def list_workspaces() -> list[dict]:
        """The workspaces of the org you're working in (every workspace, with sign-in off)."""
        a = actor.get()
        return store.workspaces(a.org_id if a else None)

    @r.get("/v1/workspaces/{workspace}/files", response_model=FilesResponse, dependencies=OPEN, tags=["files"])
    def list_files(workspace: str, content: bool = Query(False, description="Include each file's content, e.g. to export to git.")) -> dict:
        """Head: the latest version of every file."""
        return {"workspace": workspace, "files": store.head(workspace, content)}

    @r.post("/v1/workspaces/{workspace}/files", response_model=WriteResponse, responses=INVALID, dependencies=OPEN, tags=["files"])
    def edit_files(workspace: str, req: EditRequest) -> dict:
        """Change some files atomically. Rejected if the result doesn't compile. Nothing goes live until a release."""
        return {"workspace": workspace, "changed": store.edit(workspace, req.changes, req.author, req.note)}

    @r.put("/v1/workspaces/{workspace}/files", response_model=WriteResponse, responses=INVALID, dependencies=OPEN, tags=["files"])
    def replace_files(workspace: str, req: ReplaceRequest) -> dict:
        """Make head exactly these files (e.g. synced from git); files not listed are deleted."""
        return {"workspace": workspace, "changed": store.replace(workspace, without_build(req.files), req.author, req.note)}

    @r.get("/v1/workspaces/{workspace}/files/{path:path}", response_model=FileOut, dependencies=OPEN, tags=["files"])
    def get_file(workspace: str, path: str, version: int | None = None) -> dict:
        """A file at head, or at `version`."""
        return store.file(workspace, path, version)

    @r.get("/v1/workspaces/{workspace}/history/{path:path}", response_model=list[FileOut], dependencies=OPEN, tags=["files"])
    def file_history(workspace: str, path: str) -> list[dict]:
        """Every version of a file, newest first, without content."""
        return store.history(workspace, path)

    return r
