"""A workspace's GitHub repository: the connection, and pulling its sopc project."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseModel
from .files import IssueOut, WrittenFile


class SyncConflictOut(BaseModel):
    path: str
    sha: str = Field(description="The GitHub commit it conflicted at.")
    deleted_on_github: bool = Field(description="GitHub deleted the file, and it was edited here.")
    at: datetime


class SyncPrOut(BaseModel):
    url: str
    state: Literal["open", "merged", "closed"] | None


class RepoIn(BaseModel):
    owner: str = Field(min_length=1, max_length=100, description="The user or organization, e.g. `acme`.")
    repo: str = Field(min_length=1, max_length=100, description="The repository's name, e.g. `voice-agents`.")
    token: str | None = Field(
        None, max_length=512, description="A fine-grained personal access token for this repository: Contents and Pull requests "
        "read and write, Metadata read. Write-only. Leave it and `installation_id` out to keep the stored one (to change the branch or path).",
    )
    installation_id: int | None = Field(None, description="Sign in through the GitHub App's installation instead of a token: one installed "
                                        "from this workspace (`GET …/github/installations`).")
    base_branch: str = Field("main", min_length=1, max_length=255, description="The branch to pull from and open pull requests against.")
    path: str = Field("sops", max_length=512, description="The sopc project's folder in the repository (the one with sopc.yaml); empty for the root.")
    require_project: bool = Field(True, description="Check that `path` holds a sopc project (sopc.yaml). Onboarding sends false: it connects "
                                  "before there is one, and the coding agent proposes it in a pull request.")


class RepoOut(BaseModel):
    workspace: str
    owner: str
    repo: str
    base_branch: str
    path: str = Field(description="The sopc project's folder; empty for the root.")
    auth: Literal["token", "app"] = Field(description="How sopops signs in to GitHub: `token`, a fine-grained personal access token, or `app`, the GitHub App.")
    installation_id: int | None = Field(description="The GitHub App's installation, for `app`.")
    token_hint: str | None = Field(description="The token's last characters, to tell tokens apart; null for `app`. Never the token.")
    url: str = Field(description="The folder on GitHub.")
    pulled_sha: str | None = Field(description="The commit last pulled or synced: the base of the next sync.")
    pulled_at: datetime | None
    broken: str | None = Field(description="Why the connection stopped working (the App was uninstalled, suspended, or lost the repository).")
    sync_error: str | None = Field(description="Why the last sync from GitHub wrote nothing (the files don't build with the drafts, say).")
    webhook_at: datetime | None = Field(description="When GitHub last sent a webhook for the repository. While recent, polling backs off.")
    conflicts: list[SyncConflictOut] = Field(description="Files changed both on GitHub and here since the last sync, that don't merge.")
    sync_pr: SyncPrOut | None = Field(description="The pull request that sends drafts to GitHub, once opened.")
    created_at: datetime
    updated_at: datetime


class PullOut(BaseModel):
    workspace: str
    sha: str = Field(description="The base branch's head commit, now the workspace's drafts.")
    files: int = Field(description="Files in the sopc project at that commit.")
    changed: list[WrittenFile] = Field(description="New file versions (drafts). Empty if head already matched.")
    unchanged: bool = Field(description="Nothing changed: the commit was already pulled, or its files match head.")


class RepoProjectOut(BaseModel):
    workspace: str
    repo: str = Field(description="owner/name.")
    base_branch: str
    path: str = Field(description="The sopc project's folder; empty for the root.")
    sha: str = Field(description="The base branch's head commit that was read.")
    state: Literal["missing", "invalid", "valid"] = Field(
        description="missing: no folder, or no sopc.yaml in it. invalid: sopc can't build it (`issues`). valid: a sopc project sopc builds.")
    files: int = Field(description="Files in the folder (build/ skipped).")
    agents: list[str] = Field(description="The project's agents, when it's valid.")
    issues: list[IssueOut] = Field(description="sopc's issues: errors when invalid, warnings otherwise.")


class SyncOut(BaseModel):
    workspace: str
    sha: str = Field(description="The commit synced.")
    changed: list[WrittenFile] = Field(description="New file versions (drafts).")
    merged: list[str] = Field(description="Files changed both ways that merged cleanly.")
    conflicts: list[str] = Field(description="Files changed both ways that don't merge: the drafts are left alone.")
    unchanged: bool
    error: str | None = Field(description="Why nothing was written, when GitHub's files don't build with the drafts.")


class SendOut(BaseModel):
    url: str = Field(description="The sync pull request.")
    files: list[str] = Field(description="The files it changes, relative to the folder.")
    updated: bool = Field(description="An open sync pull request was updated, rather than a new one opened.")


class ConflictOut(BaseModel):
    path: str
    sha: str
    base: str | None = Field(description="The file when last synced; null if it didn't exist.")
    ours: str | None = Field(description="The draft here; null if deleted here.")
    theirs: str | None = Field(description="GitHub's file; null if deleted on GitHub.")
    at: datetime


class ResolveIn(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    take: Literal["mine", "github", "edited"] = Field(description="mine: keep the draft. github: take GitHub's. edited: write `content`.")
    content: str | None = Field(None, description="The file as edited, for `edited`.")
    author: str | None = Field(None, max_length=256)


class GitHubAppOut(BaseModel):
    configured: bool
    source: Literal["env", "db"] | None = Field(description="env: from SOPOPS_GITHUB_APP_ID and the rest. db: created from sopops.")
    app_id: str | None
    slug: str | None
    name: str | None
    html_url: str | None
    install_url: str | None
    can_create: bool = Field(description="No App yet, and SOPOPS_SECRET_KEY is set: one can be created from sopops.")
    problem: str | None = Field(description="What's missing, in words to show.")


class InstallOut(BaseModel):
    url: str = Field(description="GitHub's page to install the App, with a signed state naming the workspace.")


class InstallationOut(BaseModel):
    installation_id: int
    account: str | None


class InstallationRepoOut(BaseModel):
    owner: str
    repo: str
    full_name: str
    default_branch: str
    private: bool


class ManifestIn(BaseModel):
    public_url: str = Field(max_length=1024, description="This server's address as the browser reaches it (GitHub sends people and webhooks back to it).")
    return_to: str = Field("/", max_length=1024, description="The page to come back to.")
    organization: str | None = Field(None, max_length=100, description="Create the App in this organization; else in your account.")
    webhook_url: str | None = Field(None, max_length=1024, description="Where GitHub sends webhooks, when it can't reach `public_url` (a smee.io channel, say).")


class ManifestOut(BaseModel):
    action: str = Field(description="Where the browser posts the form.")
    manifest: str = Field(description="The form's `manifest` field (JSON).")
