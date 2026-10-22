"""The `provider_events` table."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Column, DateTime, Field, Index, SQLModel, Text, UniqueConstraint

from .columns import JSONDoc


class ProviderEvent(SQLModel, table=True):
    """One webhook delivery from a login provider, stored whether or not anything acted on it.

    sopops subscribes to every event PropelAuth can send and keeps them all: an event turned on after the fact only
    starts arriving from that moment, so the history is worth more than the rows cost. `event_type` is their string
    exactly as sent, never mapped, so a row still says what happened when sopops's own vocabulary changes
    (`sopops_server.auth.webhooks`). `message_id` is the provider's id for the delivery (Svix's `svix-id`), unique
    per provider: a retry of the same message lands on that constraint and is handled once. `acted` and `note` say
    what sopops did, if anything ("ended 2 login session(s)", "stored, nothing to do").

    Rows are append-only: nothing in the app deletes one, so this is also the audit trail of what the provider told
    us and when. Trimming old rows is an operator's job (`DELETE FROM provider_events WHERE received_at < …`).
    """

    __tablename__ = "provider_events"
    __table_args__ = (
        UniqueConstraint("provider", "message_id", name="uq_provider_events_message"),
        Index("ix_provider_events_received", "received_at"),
        Index("ix_provider_events_type", "provider", "event_type", "received_at"),
    )

    id: str = Field(primary_key=True, max_length=36)
    provider: str = Field(max_length=32, description="The login provider that sent it (`propelauth`).")
    event_type: str = Field(max_length=128, description="Their name for the event, exactly as sent.")
    message_id: str = Field(max_length=128, description="Their id for the delivery; a retry carries the same one.")
    payload: dict[str, Any] = Field(sa_column=Column(JSONDoc, nullable=False), description="The whole body as it arrived.")
    received_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    acted: bool = Field(default=False, description="Whether anything here acted on it.")
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True), description="Short note of what was done.")
