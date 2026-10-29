"""Stateless endpoints: validate, render, migrate and lint a folder of files sent with the request; nothing stored."""

from __future__ import annotations

from fastapi import APIRouter

from .. import forms
from ..schemas import (
    FilesRequest,
    LintResponse,
    MigrateFilesResponse,
    RenderResponse,
    ValidateResponse,
)
from ..sop import Invalid, without_build
from ..store import Store
from .deps import INVALID, OPEN


def router(store: Store) -> APIRouter:
    r = APIRouter()

    @r.post("/v1/validate", response_model=ValidateResponse, dependencies=OPEN, tags=["stateless"])
    def validate(req: FilesRequest) -> dict:
        issues = store.sop.validate(without_build(req.files))
        return {"valid": not any(i.severity == "error" for i in issues), "issues": [forms.locate(i).to_dict() for i in issues]}

    @r.post("/v1/render", response_model=RenderResponse, responses=INVALID, dependencies=OPEN, tags=["stateless"])
    def render(req: FilesRequest) -> dict:
        compiled = store.sop.compile(without_build(req.files))
        if not compiled.valid:
            raise Invalid(compiled.issues)
        return {
            "agents": {a: {k: v for k, v in b.items() if k != "blocks"} for a, b in compiled.agents.items()},
            "lock": compiled.lock,
            "warnings": [forms.locate(i).to_dict() for i in compiled.issues],
        }

    @r.post("/v1/migrate", response_model=MigrateFilesResponse, responses=INVALID, dependencies=OPEN, tags=["stateless"])
    def migrate_files(req: FilesRequest) -> dict:
        """Convert a folder in an older sop format with `sop migrate`: the v0.0.8 format (bases/, agent targeting),
        or `locked` (v0.0.9), which sop no longer has. Nothing stored."""
        return store.sop.migrate(without_build(req.files))

    @r.post("/v1/lint", response_model=LintResponse, responses=INVALID, dependencies=OPEN, tags=["stateless"])
    def lint(req: FilesRequest) -> dict:
        """Duplicated and conflicting text in each agent's full instructions. Advisory."""
        return {"findings": store.sop.lint(without_build(req.files))}

    return r
