"""Items (the /config endpoints): agents, shared instructions, procedures, groups and settings as forms."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .. import forms
from .base import BaseModel, Files
from .files import IssueOut, WrittenFile


class Step(BaseModel):
    text: str
    tool: str | None = Field(None, description="Exact name of a tool the agent has.")
    required: bool = Field(False, description="Steps only: the tool call must happen.")


class Saved(BaseModel):
    author: str | None = Field(None, description="Recorded with the new version.")
    note: str | None = None


class ItemMeta(BaseModel):
    version: int | None = Field(None, description="Version of the item's file at head.")
    updated_at: datetime | None = None
    updated_by: str | None = None
    file: str = Field(description="Where it lives in the sopc folder (for git export).")


class AgentFields(BaseModel):
    platform: Literal["livekit", "vapi", "elevenlabs", "retell"]
    platform_id: str = Field(description="The agent's id on its platform, e.g. LiveKit agent_name.")
    context: str = Field("", description="Text only this agent gets. It goes first in the agent's instructions.")
    blocks: list[str] = Field([], description="Shared instructions, procedures and groups, in order.")
    variables: dict[str, str] = Field({}, description="Values for {{placeholders}}; override the defaults in settings.")


class InstructionFields(BaseModel):
    text: str


class ProcedureFields(BaseModel):
    name: str
    goal: str = ""
    when: str = ""
    guidance: str = ""
    steps: list[Step] = Field(description="In order; at least one.")
    never: list[Step] = []
    warning_signs: list[Step] = []
    delivery: Literal["prompt", "auto", "tool"] = "prompt"


class GroupFields(BaseModel):
    blocks: list[str] = Field(description="Shared instructions, procedures and other groups, in order.")


class SettingsFields(BaseModel):
    variables: dict[str, str] = Field({}, description="Default values for {{placeholders}}, for every agent.")
    procedures_heading: str = Field(forms.DEFAULT_HEADING, description="Heading before the first procedure in each agent's instructions; empty for none.")


class AgentIn(AgentFields, Saved):
    pass


class InstructionIn(InstructionFields, Saved):
    pass


class ProcedureIn(ProcedureFields, Saved):
    pass


class GroupIn(GroupFields, Saved):
    pass


class SettingsIn(SettingsFields, Saved):
    pass


class NewAgent(AgentIn):
    id: str


class NewInstruction(InstructionIn):
    id: str


class NewProcedure(ProcedureIn):
    id: str


class NewGroup(GroupIn):
    id: str


class Use(BaseModel):
    agent: str
    via: str | None = Field(description="The group the agent lists it through; null if the agent lists it directly.")


class UsedBy(BaseModel):
    used_by: list[Use] = Field([], description="Which agents use it.")


class AgentConfig(AgentFields, ItemMeta):
    id: str


class InstructionConfig(InstructionFields, UsedBy, ItemMeta):
    id: str


class ProcedureConfig(ProcedureFields, UsedBy, ItemMeta):
    id: str
    format: Literal["markdown", "yaml"] = Field(description="How the file is written. New procedures are Markdown.")


class GroupConfig(GroupFields, UsedBy, ItemMeta):
    id: str


class SettingsConfig(SettingsFields, ItemMeta):
    pass


class SettingsSaved(BaseModel):
    item: SettingsConfig
    changed: list[WrittenFile]


class ConfigResponse(BaseModel):
    workspace: str
    settings: SettingsConfig
    agents: list[AgentConfig]
    instructions: list[InstructionConfig]
    procedures: list[ProcedureConfig]
    groups: list[GroupConfig] = Field(description="In the order sopc.yaml lists them.")


class BlockRequest(Saved):
    block: str = Field(description="A shared instruction, procedure or group id.")


class AgentSaved(BaseModel):
    item: AgentConfig
    changed: list[WrittenFile]


class PreviewBlock(BaseModel):
    kind: Literal["agent", "instruction", "sop"]
    id: str


class PreviewResponse(BaseModel):
    valid: bool
    issues: list[IssueOut]
    prompt: str | None = Field(description="The instructions the agent would get with these fields; null if they don't compile.")
    hash: str | None
    blocks: list[PreviewBlock] = Field(description="What the instructions are built from, in order, groups expanded.")
    release: int | None
    released_prompt: str | None = Field(description="The agent's instructions in the current release, to compare with.")


class MigrateRequest(Saved):
    apply: bool = Field(False, description="Write the converted files as a draft. Without it, only the plan is returned.")


class MigrateResponse(BaseModel):
    workspace: str
    needed: bool = Field(description="False if head is already in the current format.")
    plan: str = Field(description="sopc migrate's plan: what changes in each file, and any warnings.")
    applied: bool
    changed: list[WrittenFile]


class MigrateFilesResponse(BaseModel):
    migrated: bool = Field(description="False if the files were already in the current format.")
    plan: str
    files: Files = Field(description="The converted files (the input if nothing changed).")


class ItemVersion(BaseModel):
    version: int
    deleted: bool
    created_at: datetime
    author: str | None
    note: str | None
    file: str


class DraftItem(BaseModel):
    kind: Literal["agent", "instruction", "procedure", "group", "settings"]
    id: str
    name: str
    change: Literal["added", "edited", "deleted"]
    version: int | None
    released_version: int | None
    before: dict[str, Any] | None = Field(description="Its fields in the current release; null if added.")
    after: dict[str, Any] | None = Field(description="Its fields in the draft; null if deleted.")
