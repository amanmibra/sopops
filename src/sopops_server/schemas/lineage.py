"""Rule lineage: which rule a reworded rule replaced."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import BaseModel

LinkHow = Literal["suggestion", "matched", "manual"]


class WordingRef(BaseModel):
    """One wording of a rule: its sop id (which follows the text), the text, and the release that published it."""

    rule_id: str
    text: str
    from_release: int | None = Field(description="The release that published this wording (its lineage link's), else the first release a session ran it on.")


class LineageRule(BaseModel):
    rule_id: str
    previous_rule_id: str | None = Field(description="The rule it replaced; null for the first rule of the lineage.")
    how: LinkHow | None = Field(description="How the link was made: an applied suggestion, the matcher, or a person. Null for the first rule.")
    similarity: float | None = Field(description="Token Jaccard of the two wordings (0 to 1), for a link made at publish.")
    release_id: int | None = Field(description="The release that published the change (null for a manual link).")
    text: str | None = Field(description="Its wording, once a session ran it.")
    from_release: int | None


class RuleLineageOut(BaseModel):
    procedure_id: str
    rule_id: str
    lineage_id: str = Field(description="The id of the lineage's first rule. Rule patterns are keyed by it.")
    previous_rule_id: str | None
    how: LinkHow | None
    rules: list[LineageRule] = Field(description="Every rule of the lineage, oldest first.")


class LinkIn(BaseModel):
    previous_rule_id: str = Field(description="The rule this one replaced.")
    procedure_id: str | None = Field(None, description="Needed only when the rule id is in more than one procedure.")


class LineageRebuildOut(BaseModel):
    workspace: str
    links: int = Field(description="Links the matcher and applied suggestions made.")
    procedures: int
