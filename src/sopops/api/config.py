"""Items: the structured view of head that the UI's forms edit (agents, shared instructions, procedures, groups, settings)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, create_model

from ..schemas import (
    AgentConfig,
    AgentFields,
    AgentIn,
    AgentSaved,
    BlockRequest,
    ConfigResponse,
    GroupConfig,
    GroupIn,
    InstructionConfig,
    InstructionIn,
    ItemVersion,
    MigrateRequest,
    MigrateResponse,
    NewAgent,
    NewGroup,
    NewInstruction,
    NewProcedure,
    PreviewResponse,
    ProcedureConfig,
    ProcedureIn,
    SettingsConfig,
    SettingsIn,
    SettingsSaved,
    WriteResponse,
    WrittenFile,
)
from ..store import Store
from .deps import INVALID, OPEN


def router(store: Store) -> APIRouter:
    r = APIRouter()

    @r.get("/v1/workspaces/{workspace}/config", response_model=ConfigResponse, dependencies=OPEN, tags=["config"])
    def get_config(workspace: str) -> dict:
        """Head as structured items: settings, agents, shared instructions, procedures (SOPs) and groups.
        422 with `old_format` or `removed_field` issues if the workspace is in an older sopc format: see `…/migrate`."""
        return {"workspace": workspace, **store.items(workspace)}

    def item_routes(kind: str, plural: str, In: type[BaseModel], New: type[BaseModel], Out: type[BaseModel]) -> None:
        """GET/POST {plural}, GET/PUT/DELETE {plural}/{id}, and its history and old versions."""
        path = f"/v1/workspaces/{{workspace}}/config/{plural}"
        what = {"agent": "agent", "instruction": "shared instruction", "procedure": "procedure (SOP)", "group": "group (in sopc.yaml)"}[kind]
        Result = create_model(f"{Out.__name__}Saved", item=(Out, ...), changed=(list[WrittenFile], ...))

        def save(workspace: str, id: str, req: BaseModel, create: bool) -> dict:
            data = req.model_dump(exclude={"author", "note", "id"})
            item, changed = store.put_item(workspace, kind, id, data, req.author, req.note, create=create)
            return {"item": item, "changed": changed}

        def list_items(workspace: str) -> list[dict]:
            return store.items(workspace)[plural]

        def create(workspace: str, req) -> dict:
            return save(workspace, req.id, req, create=True)

        def get(workspace: str, id: str) -> dict:
            return store.item(workspace, kind, id)

        def put(workspace: str, id: str, req) -> dict:
            return save(workspace, id, req, create=False)

        def delete(workspace: str, id: str, author: str | None = None, note: str | None = None) -> dict:
            return {"workspace": workspace, "changed": store.delete_item(workspace, kind, id, author, note)}

        def history(workspace: str, id: str) -> list[dict]:
            return store.item_history(workspace, kind, id)

        def version(workspace: str, id: str, version: int, file: str | None = Query(None, description="From history; for a procedure that changed format.")) -> dict:
            return store.item_version(workspace, kind, id, version, file)

        create.__annotations__ = {"workspace": str, "req": New, "return": dict}
        put.__annotations__ = {"workspace": str, "id": str, "req": In, "return": dict}
        routes = [
            ("GET", "", list_items, list[Out], f"Every {what} at head.", {}),
            ("POST", "", create, Result, f"Create a {what}. Rejected if the result doesn't compile; 409 if it exists.", {**INVALID, 409: {"description": "Already exists."}}),
            ("GET", "/{id}", get, Out, f"One {what} at head.", {}),
            ("PUT", "/{id}", put, Result, f"Create or replace a {what}. Rejected if the result doesn't compile. Nothing goes live until a release.", INVALID),
            ("DELETE", "/{id}", delete, WriteResponse, f"Delete a {what}. {DELETES[kind]}", INVALID),
            ("GET", "/{id}/history", history, list[ItemVersion], f"Every version of a {what}, newest first.", {}),
            ("GET", "/{id}/versions/{version}", version, dict[str, Any], f"An older version of a {what}, as fields.", {}),
        ]
        for method, suffix, fn, model, doc, responses in routes:
            r.add_api_route(
                path + suffix, fn, methods=[method], response_model=model, description=doc, responses=responses,
                dependencies=OPEN, tags=["config"], name=OPS[fn.__name__].format(kind=kind, plural=plural), status_code=201 if method == "POST" else 200,
            )

    OPS = {
        "list_items": "config_list_{plural}",
        "create": "config_create_{kind}",
        "get": "config_get_{kind}",
        "put": "config_put_{kind}",
        "delete": "config_delete_{kind}",
        "history": "config_{kind}_history",
        "version": "config_{kind}_version",
    }
    DELETES = {
        "agent": "Nothing else changes.",
        "instruction": "It's taken out of every agent and group that lists it.",
        "procedure": "It's taken out of every agent and group that lists it.",
        "group": "Wherever it's listed, its blocks are listed in its place, so no prompt changes.",
    }
    item_routes("agent", "agents", AgentIn, NewAgent, AgentConfig)
    item_routes("instruction", "instructions", InstructionIn, NewInstruction, InstructionConfig)
    item_routes("procedure", "procedures", ProcedureIn, NewProcedure, ProcedureConfig)
    item_routes("group", "groups", GroupIn, NewGroup, GroupConfig)

    @r.post("/v1/workspaces/{workspace}/config/agents/{id}/blocks", response_model=AgentSaved, responses={**INVALID, 409: {"description": "The agent already uses it."}}, dependencies=OPEN, tags=["config"])
    def add_block(workspace: str, id: str, req: BlockRequest) -> dict:
        """Add a shared instruction, procedure or group at the end of the agent's blocks. 409 if the agent already uses it."""
        item, changed = store.add_block(workspace, id, req.block, req.author, req.note)
        return {"item": item, "changed": changed}

    @r.delete("/v1/workspaces/{workspace}/config/agents/{id}/blocks/{block}", response_model=AgentSaved, responses={**INVALID, 409: {"description": "The agent gets it through a group."}}, dependencies=OPEN, tags=["config"])
    def remove_block(workspace: str, id: str, block: str, author: str | None = None, note: str | None = None) -> dict:
        """Take a block out of the agent's blocks. 409 if the agent gets it through a group."""
        item, changed = store.remove_block(workspace, id, block, author, note)
        return {"item": item, "changed": changed}

    @r.post("/v1/workspaces/{workspace}/config/agents/{id}/preview", response_model=PreviewResponse, dependencies=OPEN, tags=["config"])
    def preview_agent(workspace: str, id: str, req: AgentFields) -> dict:
        """The prompt this agent would get with these fields, compiled with the rest of head. Saves nothing."""
        return store.preview_agent(workspace, id, req.model_dump())

    @r.post("/v1/workspaces/{workspace}/migrate", response_model=MigrateResponse, responses=INVALID, dependencies=OPEN, tags=["config"])
    def migrate(workspace: str, req: MigrateRequest | None = None) -> dict:
        """Convert a workspace stored in an older sopc format with `sopc migrate`: the v0.0.8 format, or
        `locked` (v0.0.9), which sopc no longer has. Returns the plan;
        with `apply`, writes the converted files as a draft (publish to release them)."""
        req = req or MigrateRequest()
        return store.migrate(workspace, req.apply, req.author, req.note)

    @r.get("/v1/workspaces/{workspace}/config/settings", response_model=SettingsConfig, dependencies=OPEN, tags=["config"])
    def get_settings(workspace: str) -> dict:
        return store.items(workspace)["settings"]

    @r.put("/v1/workspaces/{workspace}/config/settings", response_model=SettingsSaved, responses=INVALID, dependencies=OPEN, tags=["config"])
    def put_settings(workspace: str, req: SettingsIn) -> dict:
        item, changed = store.put_item(workspace, "settings", "settings", req.model_dump(exclude={"author", "note"}), req.author, req.note)
        return {"item": item, "changed": changed}

    @r.get("/v1/workspaces/{workspace}/config/settings/history", response_model=list[ItemVersion], dependencies=OPEN, tags=["config"])
    def settings_history(workspace: str) -> list[dict]:
        return store.item_history(workspace, "settings", "settings")

    @r.get("/v1/workspaces/{workspace}/config/settings/versions/{version}", response_model=dict[str, Any], dependencies=OPEN, tags=["config"])
    def settings_version(workspace: str, version: int) -> dict:
        return store.item_version(workspace, "settings", "settings", version)

    return r
