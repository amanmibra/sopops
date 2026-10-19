"""The `agent_delivery` table."""

from __future__ import annotations

from sqlmodel import Field, SQLModel


class AgentDelivery(SQLModel, table=True):
    """How an agent gets its prompt: `pull` (the agent, or its platform's webhook, fetches it at call start), `push`
    (sopops updates the agent on its platform when a release goes live) or `both`. No row means `pull`."""

    __tablename__ = "agent_delivery"

    workspace: str = Field(primary_key=True, max_length=128)
    agent: str = Field(primary_key=True, max_length=256)
    mode: str = Field(max_length=8)
