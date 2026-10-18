"""Suggestions: procedure edits proposed from a pattern's verdict, checked with sopc; apply as a draft or a pull request."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from ..errors import workspace_id
from ..jobs import Jobs
from ..pipeline.types import SuggestRef
from ..schemas import ApplyIn, DismissIn, SuggestionDetail, SuggestionsResponse, SuggestionStatus
from ..store import Store
from .deps import OPEN


def router(store: Store, jobs: Jobs) -> APIRouter:
    r = APIRouter()

    @r.get("/v1/workspaces/{workspace}/suggestions", response_model=SuggestionsResponse, dependencies=OPEN, tags=["suggestions"])
    def list_suggestions(workspace: str, status: SuggestionStatus | None = None) -> dict:
        """Newest first."""
        return {"items": store.suggestions.list(workspace_id(workspace), status)}

    @r.get("/v1/workspaces/{workspace}/suggestions/{suggestion_id}", response_model=SuggestionDetail, dependencies=OPEN, tags=["suggestions"])
    def get_suggestion(workspace: str, suggestion_id: str) -> dict:
        """The file before and after, the rationale, and the plan: each agent whose prompt changes, with a diff."""
        return store.suggestions.get(workspace_id(workspace), suggestion_id)

    @r.post("/v1/workspaces/{workspace}/patterns/{pattern_id}/suggest", response_model=SuggestionDetail, dependencies=OPEN, tags=["suggestions"])
    def suggest_edit(workspace: str, pattern_id: str, background: BackgroundTasks) -> dict:
        """(Re)generate a suggestion for the pattern's current verdict (real, acceptable or sop_wrong). It comes back
        `generating`; the model and sopc run as a job."""
        ws = workspace_id(workspace)
        s = store.suggestions.create(ws, pattern_id)
        if jobs.suggest(SuggestRef(workspace_id=ws, suggestion_id=s["suggestion_id"]), background) == "inline":
            return store.suggestions.get(ws, s["suggestion_id"])
        return s

    @r.post("/v1/workspaces/{workspace}/suggestions/{suggestion_id}/apply", response_model=SuggestionDetail, dependencies=OPEN, tags=["suggestions"])
    def apply_suggestion(workspace: str, suggestion_id: str, req: ApplyIn | None = None) -> dict:
        """A pull request when the workspace has a repo connection (`pr_open`), else a draft edit of the procedure
        (`applied`; it shows in Unpublished changes). 409 unless it's proposed and the procedure is unchanged since."""
        return store.suggestions.apply(workspace_id(workspace), suggestion_id, (req or ApplyIn()).reviewer)

    @r.post("/v1/workspaces/{workspace}/suggestions/{suggestion_id}/dismiss", response_model=SuggestionDetail, dependencies=OPEN, tags=["suggestions"])
    def dismiss_suggestion(workspace: str, suggestion_id: str, req: DismissIn | None = None) -> dict:
        req = req or DismissIn()
        return store.suggestions.dismiss(workspace_id(workspace), suggestion_id, req.note, req.reviewer)

    return r
