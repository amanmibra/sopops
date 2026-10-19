"""Workspace API tokens: scoped (ingest, runtime; onboarding:write comes from the import flow), shown once, stored hashed."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..schemas import TokenCreated, TokenIn, TokenOut
from ..store import Store
from ..tokens import expiry
from .deps import OPEN


def router(store: Store) -> APIRouter:
    r = APIRouter()

    @r.get("/v1/workspaces/{workspace}/tokens", response_model=list[TokenOut], dependencies=OPEN, tags=["tokens"])
    def list_tokens(workspace: str, imports: bool = Query(False, description="Include onboarding tokens made by imports.")) -> list[dict]:
        """The workspace's tokens, newest first, revoked ones included. Never the tokens themselves."""
        return store.tokens.list(workspace, imports=imports)

    @r.post("/v1/workspaces/{workspace}/tokens", response_model=TokenCreated, status_code=201, dependencies=OPEN, tags=["tokens"])
    def create_token(workspace: str, req: TokenIn | None = None) -> dict:
        """Make a token with the `ingest` and/or `runtime` scope. The token is returned here only."""
        req = req or TokenIn()
        return store.tokens.create(workspace, req.scopes, name=req.name, expires_at=expiry(req.expires_in_days))

    @r.delete("/v1/workspaces/{workspace}/tokens/{id}", response_model=TokenOut, dependencies=OPEN, tags=["tokens"])
    def revoke_token(workspace: str, id: str) -> dict:
        """Revoke a token: it stops working at once. The row is kept, with `revoked_at`."""
        return store.tokens.revoke(workspace, id)

    return r
