"""A team's AI keys and models: its own over the deployment's defaults. Keys are write-only."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..ai.keys import KeyRejected
from ..schemas import AISettingsOut, CostEstimate, ModelChoices, ModelsIn, ProviderIn, ResetWhat, TypeSafeKeyIn
from ..store import Store

WS = "/v1/workspaces/{workspace}/ai"
REJECTED = {422: {"description": "The key or model didn't work; the detail says why."}}


def router(store: Store) -> APIRouter:
    r = APIRouter(tags=["ai"])
    keys = store.ai_keys

    def checked(fn, *args):
        try:
            return fn(*args)
        except KeyRejected as e:
            raise HTTPException(422, str(e)) from e

    @r.get(WS, response_model=AISettingsOut)
    def get_ai_settings(workspace: str) -> dict:
        """The TypeSafe key, provider and models this team's checks use, and whether each is the team's own or the
        deployment's default. Never the keys."""
        return keys.status(workspace)

    @r.put(f"{WS}/typesafe", response_model=AISettingsOut, responses=REJECTED)
    def set_typesafe_key(workspace: str, req: TypeSafeKeyIn) -> dict:
        """Use this TypeSafe key for Jev, after one small decision proves it works. 503 without SOPOPS_SECRET_KEY."""
        return checked(keys.set_typesafe, workspace, req.key)

    @r.put(f"{WS}/provider", response_model=ModelChoices, responses=REJECTED)
    def set_ai_provider(workspace: str, req: ProviderIn) -> dict:
        """Use this provider, with the team's key or the deployment's. Returns the models the key can use (none for
        other). Switching providers clears the team's models."""
        return checked(keys.set_provider, workspace, req.provider, req.key, req.api_base)

    @r.get(f"{WS}/models", response_model=ModelChoices, responses=REJECTED)
    def list_ai_models(workspace: str) -> dict:
        """The models the team's provider key can use, with the recommended pair."""
        return checked(keys.models, workspace)

    @r.put(f"{WS}/models", response_model=AISettingsOut, responses=REJECTED)
    def set_ai_models(workspace: str, req: ModelsIn) -> dict:
        """Use these models, after one short call to each."""
        return checked(keys.set_models, workspace, req.fast, req.reasoning)

    @r.get(f"{WS}/estimate", response_model=CostEstimate)
    def estimate_ai_cost(workspace: str, fast: str = Query(min_length=1), reasoning: str = Query(min_length=1),
                         calls: int = Query(25000, ge=0, le=100_000_000)) -> dict:
        """What checking calls would cost with these models, per call and per month: tokens per call (the team's own
        average once it has 30 checked calls, else a typical call) times list prices, before caching or batch discounts."""
        return keys.estimate(workspace, fast, reasoning, calls)

    @r.delete(f"{WS}/{{what}}", response_model=AISettingsOut)
    def reset_ai_setting(workspace: str, what: ResetWhat) -> dict:
        """Go back to the deployment's default for the TypeSafe key, the provider (its key, base URL and the models), or
        just the models."""
        return checked(keys.reset, workspace, what)

    return r
