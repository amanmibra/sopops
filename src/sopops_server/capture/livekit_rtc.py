"""LiveKit's SDK for the recorder: rooms (`livekit`, a native library) and the server API (`livekit-api`). Optional:
`pip install 'sopops-server[livekit]'`. Imported only where a room is joined or the project is called.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from livekit import api, rtc

from .livekit import rtc_url

TOKEN_TTL = timedelta(hours=6)  # long enough to join and reconnect; each join gets its own


class RtcLink:
    """`livekit.Link` with LiveKit's SDK."""

    def room(self) -> rtc.Room:
        return rtc.Room()

    async def connect(self, room: rtc.Room, url: str, api_key: str, api_secret: str, room_name: str, identity: str) -> None:
        """Join hidden and subscribe-only: nobody in the call sees sopops, and it can't publish anything into it.
        Nothing is subscribed: text streams arrive without it, so the call's audio never comes down."""
        token = (
            api.AccessToken(api_key, api_secret)
            .with_identity(identity)
            .with_name("sopops")
            .with_ttl(TOKEN_TTL)
            .with_grants(api.VideoGrants(room_join=True, room=room_name, hidden=True, can_publish=False, can_publish_data=False,
                                         can_subscribe=True, can_update_own_metadata=False))
            .to_jwt()
        )
        await room.connect(rtc_url(url), token, rtc.RoomOptions(auto_subscribe=False))

    async def dispatches(self, url: str, api_key: str, api_secret: str, room_name: str) -> list[tuple[str, str]]:
        """Each agent dispatched to the room: its agent name and job metadata."""
        lk = api.LiveKitAPI(url, api_key, api_secret)
        try:
            return [(d.agent_name, d.metadata) for d in await lk.agent_dispatch.list_dispatch(room_name)]
        finally:
            await lk.aclose()

    def is_agent(self, participant: Any) -> bool:
        return getattr(participant, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT

    def room_closed(self, reason: Any) -> bool:
        return reason in (rtc.DisconnectReason.ROOM_DELETED, rtc.DisconnectReason.ROOM_CLOSED)


async def test_credentials(url: str, api_key: str, api_secret: str) -> int:
    """List the project's rooms (read-only). Returns how many are open; raises on a refused key or no answer."""
    lk = api.LiveKitAPI(url, api_key, api_secret)
    try:
        res = await lk.room.list_rooms(api.ListRoomsRequest())
        return len(res.rooms)
    finally:
        await lk.aclose()


def twirp_status(e: Exception) -> int | None:
    return e.status if isinstance(e, api.TwirpError) else None
