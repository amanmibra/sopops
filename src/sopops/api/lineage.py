"""Rule lineage: which rule a reworded rule replaced; linking and unlinking by hand when the matcher got it wrong."""

from __future__ import annotations

from fastapi import APIRouter

from ..errors import workspace_id
from ..schemas import LineageRebuildOut, LinkIn, RuleLineageOut
from ..store import Store
from .deps import OPEN


def router(store: Store) -> APIRouter:
    r = APIRouter()

    @r.get("/v1/workspaces/{workspace}/rules/{rule_id}/lineage", response_model=RuleLineageOut, dependencies=OPEN, tags=["lineage"])
    def get_rule_lineage(workspace: str, rule_id: str, procedure: str | None = None) -> dict:
        """The rule's lineage: the rule it replaced, how that was decided, and every wording, oldest first.
        `procedure` only when the rule id is in more than one procedure."""
        return store.lineage.get(workspace_id(workspace), rule_id, procedure)

    @r.post("/v1/workspaces/{workspace}/rules/{rule_id}/lineage", response_model=RuleLineageOut, dependencies=OPEN, tags=["lineage"])
    def link_rule(workspace: str, rule_id: str, req: LinkIn) -> dict:
        """Say the rule replaced `previous_rule_id` (a `manual` link, which publishing and rebuilding never change).
        Its findings join that rule's pattern, with its verdict and review state. 409 if it would make a cycle."""
        return store.lineage.link(workspace_id(workspace), rule_id, req.previous_rule_id, req.procedure_id)

    @r.delete("/v1/workspaces/{workspace}/rules/{rule_id}/lineage", response_model=RuleLineageOut, dependencies=OPEN, tags=["lineage"])
    def unlink_rule(workspace: str, rule_id: str, procedure: str | None = None) -> dict:
        """Say the rule is a new rule, not a rewording (kept as a `manual` row). Its findings, and those of the
        wordings after it, move to a rule pattern of their own."""
        return store.lineage.link(workspace_id(workspace), rule_id, None, procedure)

    @r.post("/v1/workspaces/{workspace}/rules/lineage/rebuild", response_model=LineageRebuildOut, dependencies=OPEN, tags=["lineage"])
    def rebuild_lineage(workspace: str) -> dict:
        """Recompute the links of every release against the one before it (manual links stay), and move patterns to
        match. Idempotent; `sopops lineage rebuild` does the same."""
        return store.lineage.rebuild(workspace_id(workspace))

    return r
