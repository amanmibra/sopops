"""A workspace's GitHub repository: the connection, and pulling its sopc project."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import BaseModel
from .files import WrittenFile


class RepoIn(BaseModel):
    owner: str = Field(min_length=1, max_length=100, description="The user or organization, e.g. `acme`.")
    repo: str = Field(min_length=1, max_length=100, description="The repository's name, e.g. `voice-agents`.")
    token: str | None = Field(
        None, max_length=512, description="A fine-grained personal access token for this repository: Contents and Pull requests "
        "read and write, Metadata read. Write-only. Leave it out to keep the stored one (to change the branch or path).",
    )
    base_branch: str = Field("main", min_length=1, max_length=255, description="The branch to pull from and open pull requests against.")
    path: str = Field("sop", max_length=512, description="The sopc project's folder in the repository (the one with sopc.yaml); empty for the root.")


class RepoOut(BaseModel):
    workspace: str
    owner: str
    repo: str
    base_branch: str
    path: str = Field(description="The sopc project's folder; empty for the root.")
    auth: Literal["token"] = Field(description="How sopops signs in to GitHub: `token`, a fine-grained personal access token.")
    token_hint: str = Field(description="The token's last characters, to tell tokens apart. Never the token.")
    url: str = Field(description="The folder on GitHub.")
    pulled_sha: str | None = Field(description="The commit last pulled.")
    pulled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PullOut(BaseModel):
    workspace: str
    sha: str = Field(description="The base branch's head commit, now the workspace's drafts.")
    files: int = Field(description="Files in the sopc project at that commit.")
    changed: list[WrittenFile] = Field(description="New file versions (drafts). Empty if head already matched.")
    unchanged: bool = Field(description="Nothing changed: the commit was already pulled, or its files match head.")
