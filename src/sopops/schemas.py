"""API request and response shapes (Pydantic). Separate from the table models in `models`, so database-only fields
never reach the API, and the OpenAPI spec (and the TypeScript client generated from it) stays stable."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel as _BaseModel, ConfigDict, Field

from . import forms

Files = dict[str, str]


class BaseModel(_BaseModel):
    # Responses always include fields that have defaults, so the spec (and generated types) mark them required.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class FilesRequest(BaseModel):
    files: Files = Field(description="Source files keyed by path relative to the sopc folder, e.g. 'instructions/brand-voice.md'. Paths under build/ are ignored.")


class IssueOut(BaseModel):
    code: str
    message: str = Field(description="sopc's message.")
    path: str = Field(description="Relative to the sopc folder; empty for folder-wide issues.")
    severity: Literal["error", "warning"]
    kind: Literal["agent", "instruction", "procedure", "group", "settings"] | None = Field(None, description="The item the issue is about, if known.")
    id: str | None = None
    field: str | None = Field(None, description="The item's field, as the /config endpoints name it (e.g. `steps`, `blocks`).")
    index: int | None = Field(None, description="For list fields (steps, never, warning_signs, blocks): which entry.")
    text: str | None = Field(None, description="The issue in plain words, for showing next to the field.")


class ValidateResponse(BaseModel):
    valid: bool
    issues: list[IssueOut]


class RenderedAgent(BaseModel):
    platform_ref: str
    hash: str
    prompt: str
    tools: list[str]
    sops: dict[str, dict[str, Any]] = Field(description="Tool-delivered SOPs (`delivery: auto | tool`) keyed by SOP id: what `get_sop` returns.")


class RenderResponse(BaseModel):
    agents: dict[str, RenderedAgent]
    lock: dict[str, Any] = Field(description="sopc's build/lock.json.")
    warnings: list[IssueOut]


class LintSource(BaseModel):
    block: str
    text: str


class LintFinding(BaseModel):
    code: str
    message: str
    sources: list[LintSource]
    agents: list[str]


class LintResponse(BaseModel):
    findings: list[LintFinding]


class WorkspaceOut(BaseModel):
    workspace: str
    current_release: int | None


class FileOut(BaseModel):
    path: str
    version: int
    deleted: bool
    created_at: datetime
    author: str | None
    note: str | None
    content: str | None = Field(None, description="Only when requested (`?content=true`, or a single file).")


class FilesResponse(BaseModel):
    workspace: str
    files: list[FileOut]


class EditRequest(BaseModel):
    changes: dict[str, str | None] = Field(description="{path: new content}, or null to delete the file.")
    author: str | None = None
    note: str | None = None


class ReplaceRequest(FilesRequest):
    author: str | None = None
    note: str | None = None


class WrittenFile(BaseModel):
    path: str
    version: int
    deleted: bool


class WriteResponse(BaseModel):
    workspace: str
    changed: list[WrittenFile] = Field(description="New file versions. Empty if nothing changed.")


class Component(BaseModel):
    kind: Literal["agent", "instruction", "sop", "config", "base"] = Field(description="`base` only in releases made before sopc v0.0.9.")
    id: str
    path: str | None
    version: int | None


class FileChange(BaseModel):
    path: str
    change: Literal["added", "edited", "deleted"]
    version: int | None = Field(description="Head version; null if deleted.")
    released_version: int | None = Field(description="Version in the current release; null if added.")


class DraftAgent(BaseModel):
    agent: str
    platform_ref: str
    status: Literal["added", "changed", "removed", "unchanged"]
    hash: str | None
    released_hash: str | None
    prompt: str | None = Field(description="The draft prompt; null if removed.")
    released_prompt: str | None = Field(description="The prompt in the current release; null if added.")
    components: list[Component]


class DraftResponse(BaseModel):
    workspace: str
    release: int | None = Field(description="The current release the draft is compared with.")
    valid: bool
    issues: list[IssueOut]
    files: list[FileChange] = Field(description="Files changed since the current release.")
    items: list[DraftItem] = Field(description="The same changes as agents, shared instructions, procedures, groups and settings.")
    agents: list[DraftAgent]
    lint: list[LintFinding] = Field([], description="`sopc lint` on the draft: duplicated or conflicting instructions. Advisory; publishing isn't blocked.")


class ReleaseRequest(BaseModel):
    author: str | None = None
    note: str | None = None


class ReleaseSummary(BaseModel):
    number: int
    created_at: datetime
    author: str | None
    note: str | None
    files: dict[str, int] = Field(description="{path: version} snapshot.")
    current: bool


class ReleaseResponse(BaseModel):
    release: ReleaseSummary
    created: bool = Field(description="False if head was already the current release.")


class PublishRequest(ReplaceRequest):
    pass


class PublishResponse(ReleaseResponse):
    changed: list[WrittenFile]


class ReleasedAgent(BaseModel):
    platform_ref: str
    hash: str
    prompt: str
    tools: list[str]
    tool_sops: list[str]
    components: list[Component]


class ReleaseDetail(ReleaseSummary):
    agents: dict[str, ReleasedAgent]


class AgentSummary(BaseModel):
    agent: str
    platform_ref: str
    hash: str


class AgentsResponse(BaseModel):
    workspace: str
    release: int
    agents: list[AgentSummary]


class AgentOut(BaseModel):
    agent: str = Field(description="sopc agent id.")
    platform_ref: str
    release: int
    hash: str
    prompt: str
    tools: list[str]
    tool_sops: list[str] = Field(description="Ids of SOPs to fetch with `get_sop` (`/sops/{sop_id}`).")
    components: list[Component] = Field(description="Each file the prompt was built from, at its version. Put this and `release` in call metadata.")


class FetchOut(BaseModel):
    id: int
    agent: str
    release: int
    hash: str
    at: datetime


# --- items (the /config endpoints) ----------------------------------------------------

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
    context: str = Field("", description="Text only this agent gets. It goes first in the prompt.")
    blocks: list[str] = Field([], description="Shared instructions, procedures and groups, in prompt order.")
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
    procedures_heading: str = Field(forms.DEFAULT_HEADING, description="Heading before the first procedure in each prompt; empty for none.")


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
    prompt: str | None = Field(description="The prompt the agent would get with these fields; null if it doesn't compile.")
    hash: str | None
    blocks: list[PreviewBlock] = Field(description="What the prompt is built from, in order, groups expanded.")
    release: int | None
    released_prompt: str | None = Field(description="The agent's prompt in the current release, to compare with.")


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
