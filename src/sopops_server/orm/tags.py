"""The `agent_tags` and `session_tags` tables."""

from __future__ import annotations

from sqlmodel import Field, ForeignKeyConstraint, Index, SQLModel


class AgentTag(SQLModel, table=True):
    """A label on an agent (`legacy`, `sopops`, `pilot`): set in sopops, not in the agent's sop file, so changing one
    needs no release. A session copies its agent's tags when it comes in."""

    __tablename__ = "agent_tags"

    workspace: str = Field(primary_key=True, max_length=128)
    agent: str = Field(primary_key=True, max_length=256)
    tag: str = Field(primary_key=True, max_length=64)


class SessionTag(SQLModel, table=True):
    """A label on a session: its agent's tags when it came in, and any the sender passed. Kept as they were when the
    agent's tags change later."""

    __tablename__ = "session_tags"
    __table_args__ = (
        Index("ix_session_tags_workspace_tag", "workspace_id", "tag"),
        ForeignKeyConstraint(["session_id"], ["sessions.id"], name="fk_session_tags_session", ondelete="CASCADE"),
    )

    session_id: str = Field(primary_key=True, max_length=36)
    tag: str = Field(primary_key=True, max_length=64)
    workspace_id: str = Field(max_length=128)
