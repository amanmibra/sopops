"""A workspace's GitHub repository: connect it, pull its sop project into drafts, keep the two in sync both ways
(`sopops_server.repo_sync`). Pull requests also come from suggestions (`sopops_server.github.open_pull_request`)."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response

from .. import github, repo_sync
from ..errors import NotFound
from ..schemas.repo import ConflictOut, PullOut, RepoIn, RepoOut, RepoProjectOut, ResolveIn, SendOut, SyncOut
from ..store import Store
from .deps import INVALID, OPEN

GITHUB = {
    400: {"description": "GitHub refused the token, can't find the repository, branch or sop project, or rejected a request."},
    429: {"description": "GitHub's rate limit for the token is used up."},
    502: {"description": "GitHub failed or couldn't be reached."},
    503: {"description": "SOPOPS_SECRET_KEY isn't set, so the token can't be stored or read."},
}


def router(store: Store) -> APIRouter:
    r = APIRouter()
    path = "/v1/workspaces/{workspace}/repo"

    @r.get(path, response_model=RepoOut | None, dependencies=OPEN, tags=["repo"])
    def get_repo(workspace: str, background: BackgroundTasks) -> dict | None:
        """The workspace's GitHub repository, or null when none is connected. Never the token. Reading it polls GitHub
        in the background when it's due (the fallback for webhooks, which can't reach a server on 127.0.0.1); what
        changes arrives on the live stream."""
        try:
            out = github.get(store, workspace)
        except NotFound:
            return None
        conn = github.repo_connection(store, workspace)
        if conn is not None and (repo_sync.due(conn) or conn.sync_pr_state == "open"):
            background.add_task(repo_sync.poll_one, store, workspace)
        return out

    @r.post(path, response_model=RepoOut, dependencies=OPEN, tags=["repo"], responses=GITHUB)
    def connect_repo(workspace: str, req: RepoIn) -> dict:
        """Connect a GitHub repository, or change the connection. Checks that the token can read the repository and the
        branch and (unless `require_project` is false) that `path` on `base_branch` holds a sop project (sop.yaml);
        the token is stored encrypted with SOPOPS_SECRET_KEY."""
        return github.connect(store, workspace, req.owner, req.repo, req.token, req.base_branch, req.path, req.require_project, req.installation_id)

    @r.get(f"{path}/project", response_model=RepoProjectOut, dependencies=OPEN, tags=["repo"], responses={**GITHUB, 404: {"description": "No repository connected."}})
    def inspect_repo_project(workspace: str) -> dict:
        """What the connected folder holds at the base branch's head, compiled with sop: `missing`, `invalid` (with
        sop's issues) or `valid`. Changes nothing; onboarding uses it to choose between importing the project and
        having a coding agent propose one."""
        return github.inspect_project(store, workspace)

    @r.delete(path, status_code=204, dependencies=OPEN, tags=["repo"], responses={404: {"description": "No repository connected."}})
    def disconnect_repo(workspace: str) -> Response:
        """Forget the repository and its token. The workspace's files stay."""
        github.disconnect(store, workspace)
        return Response(status_code=204)

    @r.post(f"{path}/pull", response_model=PullOut, dependencies=OPEN, tags=["repo"], responses={**GITHUB, **INVALID})
    def pull_repo(workspace: str, author: str | None = None) -> dict:
        """Make the workspace's head the sop project at the base branch's head, as drafts: files not in the repository
        are deleted. Publish from Unpublished changes as usual. Pulling a commit already pulled is a no-op."""
        return github.pull(store, workspace, author)

    @r.post(f"{path}/sync", response_model=SyncOut, dependencies=OPEN, tags=["repo"], responses=GITHUB)
    def sync_repo(workspace: str, author: str | None = None) -> dict:
        """Sync now: bring the folder at the base branch's head into the drafts. Files changed only on GitHub are
        written, files changed both ways are merged, and ones that don't merge are left as they are here and listed in
        `conflicts`. Nothing is published."""
        return repo_sync.sync(store, workspace, author=author)

    @r.post(f"{path}/send", response_model=SendOut, dependencies=OPEN, tags=["repo"],
            responses={**GITHUB, 409: {"description": "Nothing to send, unresolved sync conflicts, or never synced."}})
    def send_repo_drafts(workspace: str) -> dict:
        """Send drafts to GitHub: open the sync pull request (branch `sopops/sync`) with the drafts' differences from
        the base branch, or update the open one. Syncs from GitHub first."""
        return repo_sync.send(store, workspace)

    @r.get(f"{path}/conflict", response_model=ConflictOut, dependencies=OPEN, tags=["repo"], responses={404: {"description": "No such conflict."}})
    def get_repo_conflict(workspace: str, path: str) -> dict:
        """A sync conflict's three versions: when last synced, the draft here, and GitHub's."""
        return repo_sync.conflict(store, workspace, path)

    @r.post(f"{path}/conflict", response_model=RepoOut, dependencies=OPEN, tags=["repo"], responses={**INVALID, 404: {"description": "No such conflict."}})
    def resolve_repo_conflict(workspace: str, req: ResolveIn) -> dict:
        """Settle a sync conflict: keep yours (it goes with the next Send to GitHub), take GitHub's, or save an edit."""
        try:
            return repo_sync.resolve(store, workspace, req.path, req.take, req.content, req.author)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    return r
