"""Database tables, as SQLModel table models. API request and response shapes live in `schemas`, so these never leak.

The schema is managed by Alembic (`sopops/migrations`); `db.upgrade` brings a database to head on startup.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, ForeignKeyConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# JSONB on Postgres (indexable, compact), JSON elsewhere.
JSONDoc = JSON().with_variant(JSONB(), "postgresql")


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"

    workspace: str = Field(primary_key=True, max_length=128)
    current_release: int | None = Field(default=None, description="The release agents are served.")


class FileVersion(SQLModel, table=True):
    """One version of one file in a workspace's sopc folder. `content` is NULL in a version that deletes the file."""

    __tablename__ = "file_versions"

    workspace: str = Field(primary_key=True, max_length=128)
    path: str = Field(primary_key=True, max_length=512)
    version: int = Field(primary_key=True)
    content: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    author: str | None = Field(default=None, max_length=256)
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class Release(SQLModel, table=True):
    """An immutable {path: version} snapshot plus its compiled build."""

    __tablename__ = "releases"

    workspace: str = Field(primary_key=True, max_length=128)
    number: int = Field(primary_key=True)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    author: str | None = Field(default=None, max_length=256)
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    files: dict[str, int] = Field(sa_column=Column(JSON, nullable=False))
    # {"agents": {id: {platform_ref, hash, prompt, tools, sops, components}}}
    build: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))


class Fetch(SQLModel, table=True):
    """A call-start lookup: which release and prompt hash an agent was served."""

    __tablename__ = "fetches"

    id: int | None = Field(default=None, primary_key=True)
    workspace: str = Field(max_length=128, index=True)
    agent: str = Field(max_length=256)
    release: int
    hash: str = Field(max_length=128)
    at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class SessionRecord(SQLModel, table=True):
    """One conversation an agent had: a voice call or a text chat. Unique on (workspace, platform, external_id), so
    re-sending a session (a retried webhook, a re-run import) updates it instead of adding a second one."""

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "platform", "external_id", name="uq_sessions_key"),
        Index("ix_sessions_workspace_started", "workspace_id", "started_at"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.workspace"], name="fk_sessions_workspace"),
        ForeignKeyConstraint(["workspace_id", "release_id"], ["releases.workspace", "releases.number"], name="fk_sessions_release"),
    )

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    agent_id: str | None = Field(default=None, max_length=256, description="The sopc agent, when the payload's agent could be matched.")
    agent_ref: str | None = Field(default=None, max_length=256, description="The agent as the payload named it.")
    channel: str = Field(max_length=16)  # voice | text
    platform: str = Field(max_length=32)  # livekit | elevenlabs | vapi | otel | import | api (schemas.Platform)
    external_id: str = Field(max_length=256)
    release_id: int | None = Field(default=None, description="The sopops release the session ran on.")
    version_ref: str | None = Field(default=None, max_length=256, description="The platform's own version id.")
    started_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    ended_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    status: str = Field(max_length=16)  # received | routed | checked | excluded | partial | failed
    status_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    # [{role: agent|user|tool|system, text, offset_s, ...}], in order
    turns: list[dict[str, Any]] = Field(sa_column=Column(JSONDoc, nullable=False))
    tool_calls: list[dict[str, Any]] = Field(sa_column=Column(JSONDoc, nullable=False))
    meta: dict[str, Any] = Field(sa_column=Column("metadata", JSONDoc, nullable=False))
    payload_hash: str = Field(max_length=64, description="sha256 of the canonical payload: an identical re-send changes nothing.")
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    processed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    # The worst procedure result once checked: deviated | partial | followed | not_measurable (see pipeline.rollup)
    result: str | None = Field(default=None, max_length=16)


class ProcedureCheck(SQLModel, table=True):
    """One session x one procedure the router considered: whether it applied, was excluded (the session ended before
    the procedure started or finished), and, once checked, the rolled-up result. Rewritten whole by each run."""

    __tablename__ = "procedure_checks"
    __table_args__ = (
        UniqueConstraint("session_id", "procedure_id", name="uq_procedure_checks_session_procedure"),
        Index("ix_procedure_checks_workspace_procedure", "workspace_id", "procedure_id"),
        ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_procedure_checks_session", ondelete="CASCADE"),
    )

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    session_id: str = Field(max_length=36)
    procedure_id: str = Field(max_length=256)
    procedure_name: str | None = Field(default=None, max_length=512)
    procedure_version: int | None = Field(default=None, description="The procedure file's version in the release it was checked against.")
    release_id: int | None = Field(default=None, description="The release the procedure was read from.")
    applicable: bool
    excluded_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    route_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    result: str | None = Field(default=None, max_length=16)  # deviated | partial | followed | not_measurable; null until checked
    goal_reached: bool | None = None
    goal_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    steps_applicable: int | None = None
    steps_done: int | None = None
    nevers_violated: int | None = None
    model: str | None = Field(default=None, max_length=256, description="The model that made the last call for this row.")
    prompt_version: str | None = Field(default=None, max_length=64)
    revision: str = Field(max_length=64, description="The session payload hash this row was computed from.")
    checked_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class RuleResult(SQLModel, table=True):
    """One session x one rule (step, never or warning sign) of a checked procedure."""

    __tablename__ = "rule_results"
    __table_args__ = (
        UniqueConstraint("session_id", "procedure_id", "rule_id", name="uq_rule_results_session_rule"),
        Index("ix_rule_results_procedure_rule", "workspace_id", "procedure_id", "rule_id", "session_id"),
        ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_rule_results_session", ondelete="CASCADE"),
    )

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    session_id: str = Field(max_length=36)
    procedure_id: str = Field(max_length=256)
    rule_id: str = Field(max_length=64, description="sopc's stable rule id (lock.json, `sopc export`).")
    kind: str = Field(max_length=16)  # step | never | warning
    index: int = Field(description="1-based position within its kind.")
    rule_text: str = Field(sa_column=Column(Text, nullable=False))
    rule_text_hash: str = Field(max_length=64)
    status: str = Field(max_length=16)  # step: done|out_of_order|skipped|not_reached|not_applicable; never: violated|ok; warning: present|absent
    evidence: list[dict[str, Any]] = Field(sa_column=Column(JSONDoc, nullable=False))  # [{turn, quote, source}]
    reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class Finding(SQLModel, table=True):
    """A rule result that is a deviation (see `pipeline.rollup.FINDING_STATUSES`). Patterns (M3) group findings."""

    __tablename__ = "findings"
    __table_args__ = (
        Index("ix_findings_workspace_rule", "workspace_id", "procedure_id", "rule_id"),
        Index("ix_findings_pattern", "pattern_id"),
        ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_findings_session", ondelete="CASCADE"),
        ForeignKeyConstraint(["rule_result_id"], ["rule_results.id"], name="fk_findings_rule_result", ondelete="CASCADE"),
    )

    id: str = Field(primary_key=True, max_length=36)
    workspace_id: str = Field(max_length=128)
    session_id: str = Field(max_length=36)
    procedure_id: str = Field(max_length=256)
    rule_id: str = Field(max_length=64)
    rule_result_id: str = Field(max_length=36)
    status: str = Field(max_length=16)
    pattern_id: str | None = Field(default=None, max_length=36)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class IngestToken(SQLModel, table=True):
    """A workspace's ingest token, stored as a sha256 hash. Shown once, when created or rotated."""

    __tablename__ = "ingest_tokens"

    workspace: str = Field(sa_column=Column(String(128), primary_key=True))
    token_hash: str = Field(max_length=64, unique=True)
    prefix: str = Field(max_length=32)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


metadata = SQLModel.metadata
