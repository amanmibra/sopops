"""A workspace's GitHub repository: connect it, pull its sopc project into drafts. Pull requests come from
suggestions (`sopops.github.open_pull_request`)."""

from __future__ import annotations

from fastapi import APIRouter, Response

from .. import github
from ..schemas.repo import PullOut, RepoIn, RepoOut
from ..store import Store
from .deps import INVALID, OPEN

GITHUB = {
    400: {"description": "GitHub refused the token, can't find the repository, branch or sopc project, or rejected a request."},
    429: {"description": "GitHub's rate limit for the token is used up."},
    502: {"description": "GitHub failed or couldn't be reached."},
    503: {"description": "SOPOPS_SECRET_KEY isn't set, so the token can't be stored or read."},
}


def router(store: Store) -> APIRouter:
    r = APIRouter()
    path = "/v1/workspaces/{workspace}/repo"

    @r.get(path, response_model=RepoOut, dependencies=OPEN, tags=["repo"], responses={404: {"description": "No repository connected."}})
    def get_repo(workspace: str) -> dict:
        """The workspace's GitHub repository. Never the token."""
        return github.get(store, workspace)

    @r.post(path, response_model=RepoOut, dependencies=OPEN, tags=["repo"], responses=GITHUB)
    def connect_repo(workspace: str, req: RepoIn) -> dict:
        """Connect a GitHub repository, or change the connection. Checks that the token can read the repository and that
        `path` on `base_branch` holds a sopc project (sopc.yaml); the token is stored encrypted with SOPOPS_SECRET_KEY."""
        return github.connect(store, workspace, req.owner, req.repo, req.token, req.base_branch, req.path)

    @r.delete(path, status_code=204, dependencies=OPEN, tags=["repo"], responses={404: {"description": "No repository connected."}})
    def disconnect_repo(workspace: str) -> Response:
        """Forget the repository and its token. The workspace's files stay."""
        github.disconnect(store, workspace)
        return Response(status_code=204)

    @r.post(f"{path}/pull", response_model=PullOut, dependencies=OPEN, tags=["repo"], responses={**GITHUB, **INVALID})
    def pull_repo(workspace: str, author: str | None = None) -> dict:
        """Make the workspace's head the sopc project at the base branch's head, as drafts: files not in the repository
        are deleted. Publish from Unpublished changes as usual. Pulling a commit already pulled is a no-op."""
        return github.pull(store, workspace, author)

    return r
