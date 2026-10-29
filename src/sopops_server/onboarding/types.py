"""The shapes onboarding works with: source prompts in, a proposal out, and what the LLM returns at each step."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Platform = Literal["livekit", "elevenlabs", "vapi", "retell"]
SegmentKind = Literal["context", "instruction", "procedure", "heading"]


# --- input ------------------------------------------------------------------------------


class SourcePrompt(BaseModel):
    """One existing agent's instructions, as they run today."""

    name: str = Field(description="The agent's name; its sop id is made from it.", min_length=1, max_length=200)
    platform: Platform = "livekit"
    platform_id: str | None = Field(None, description="The agent's id on its platform (LiveKit agent_name, ElevenLabs agent_id, Vapi assistant id). Default: the agent's id.")
    prompt: str = Field(description="The instructions, verbatim.", min_length=1)
    tools: list[str] = Field(default_factory=list, description="Tool names the agent can call, so procedure steps can name them.")


# --- what the LLM returns ------------------------------------------------------------------


class Segment(BaseModel):
    units: list[int] = Field(description="The numbers of the units in this segment, consecutive, in order.")
    kind: SegmentKind
    title: str = Field(description="A short name for what the segment is about, e.g. 'Brand voice' or 'Reservations'.")


class Segmentation(BaseModel):
    segments: list[Segment]


class StepDraft(BaseModel):
    text: str
    tool: str | None = None
    required: bool = False


class ProcedureDraft(BaseModel):
    name: str
    goal: str = ""
    goal_inferred: bool = Field(False, description="True if the source didn't state a goal and you wrote one.")
    when: str = ""
    guidance: str = ""
    steps: list[StepDraft] = Field(default_factory=list)
    never: list[StepDraft] = Field(default_factory=list)
    warning_signs: list[StepDraft] = Field(default_factory=list)




# --- the proposal ---------------------------------------------------------------------------


class ProposedOption(BaseModel):
    text: str
    tool: str | None = None
    required: bool = False


class ProposedStep(ProposedOption):
    # `or` is a Python keyword: the field is `or_`, read and written as `or` (as `schemas.config.Step`).
    model_config = ConfigDict(validate_by_name=True, serialize_by_alias=True)

    or_: list[ProposedOption] = Field([], alias="or")  # steps only: OR options


class ProposedInstruction(BaseModel):
    id: str
    title: str = ""
    text: str
    used_by: list[str] = Field(default_factory=list)


class ProposedProcedure(BaseModel):
    id: str
    name: str
    goal: str = ""
    goal_inferred: bool = False
    when: str = ""
    guidance: str = ""
    steps: list[ProposedStep] = Field(default_factory=list)
    never: list[ProposedStep] = Field(default_factory=list)
    warning_signs: list[ProposedStep] = Field(default_factory=list)
    delivery: Literal["prompt", "auto", "tool"] = "prompt"
    used_by: list[str] = Field(default_factory=list)
    source: str = Field("", description="The source text it was structured from, with {{variables}}.")


class ProposedGroup(BaseModel):
    id: str
    blocks: list[str]


class ProposedAgent(BaseModel):
    id: str
    name: str = ""
    platform: Platform = "livekit"
    platform_id: str = ""
    context: str = ""
    blocks: list[str] = Field(default_factory=list)
    variables: dict[str, str] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)


class ProposedSettings(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict)
    procedures_heading: str = "## Procedures"


class Variant(BaseModel):
    agents: list[str]
    text: str
    block: str | None = None


class Finding(BaseModel):
    """Something the reviewer should know or decide.

    - `duplicate`: the same text in several agents, now one block.
    - `values`: near-copies that differ only by a value (a name, a link, a number), now one block with a variable.
    - `drift`: agents that say different things about the same topic. Kept as each agent had it (a variable, or a block
      per version) until someone picks one.
    """

    id: str
    kind: Literal["duplicate", "values", "drift"]
    title: str
    message: str
    block: str | None = None
    variable: str | None = None
    variants: list[Variant] = Field(default_factory=list)
    resolved: str | None = Field(None, description="'keep', or the agent whose version everyone now gets.")


class Changed(BaseModel):
    original: str
    now: str


class AgentFidelity(BaseModel):
    """How the agent's full instructions cover its original (`sop compare`, sentence by sentence)."""

    agent: str
    ok: bool
    score: float = Field(description="Share of the original's sentences kept (0-1). Reworded ones count as kept.")
    similarity: float = Field(description="Word-level similarity of the whole texts (0-1); lower when text moved.")
    sentences: int
    missing: list[str] = Field(default_factory=list)
    changed: list[Changed] = Field(default_factory=list)
    reworded: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    rules: int = Field(0, description="Procedure rules (steps, never rules, warning signs) the full instructions state.")
    rules_verbatim: int = Field(0, description="Of those, how many appear word for word in the original (spacing and case aside).")
    not_verbatim: list[str] = Field(default_factory=list, description="The rules that don't: reworded, merged, or not in the original.")
    compiled: str | None = None


class ProposalIssue(BaseModel):
    code: str
    message: str
    path: str = ""
    severity: str = "error"
    kind: str | None = None
    id: str | None = None
    field: str | None = None
    index: int | None = None
    text: str | None = None


class ProposalContent(BaseModel):
    """What gets written: blocks, groups, agents and settings. Edit these, then check fidelity again."""

    agents: list[ProposedAgent] = Field(default_factory=list)
    instructions: list[ProposedInstruction] = Field(default_factory=list)
    procedures: list[ProposedProcedure] = Field(default_factory=list)
    groups: list[ProposedGroup] = Field(default_factory=list)
    settings: ProposedSettings = Field(default_factory=ProposedSettings)


class Proposal(ProposalContent):
    findings: list[Finding] = Field(default_factory=list)
    fidelity: list[AgentFidelity] = Field(default_factory=list)
    issues: list[ProposalIssue] = Field(default_factory=list, description="sop's checks of the proposal as files.")
    notes: list[str] = Field(default_factory=list)
    originals: dict[str, str] = Field(default_factory=dict, description="Each agent's original instructions, by agent id.")
    decisions: dict[str, str] = Field(default_factory=dict, description="The reviewer's decisions, by the id of the group they're about (sop overlap's ids).")
    files: dict[str, str] = Field(default_factory=dict, description="An imported sop project's files: the import is these files, written as drafts exactly.")
