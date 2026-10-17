"""Send each LiveKit session to sopops when it ends. Copy this file next to your agent.

    session = AgentSession(..., userdata={"sopops_release": sop["release"]})  # or an object with .sopops_release
    record_session(ctx, session)

Env: SOPOPS_URL (http://sopops:8484/v1/workspaces/<ws>) and SOPOPS_INGEST_TOKEN (Settings in sopops).
"""

import os
from datetime import datetime, timezone

import httpx
from livekit.agents import AgentSession, JobContext


def record_session(ctx: JobContext, session: AgentSession, agent: str | None = None) -> None:
    started = datetime.now(timezone.utc)

    async def send(reason: str = "") -> None:
        turns, tools = [], {}
        for item in session.history.to_dict(exclude_timestamp=False)["items"]:
            at, kind = item.get("created_at"), item["type"]
            if kind == "message":
                text = " ".join(c for c in item.get("content", []) if isinstance(c, str))
                turns.append({"role": item["role"], "text": text, "at": at, "interrupted": item.get("interrupted", False)})
            elif kind == "function_call":
                turns.append({"role": "tool", "text": f"{item['name']}({item['arguments']})", "name": item["name"], "at": at})
                tools[item["call_id"]] = {"name": item["name"], "arguments": item["arguments"], "call_id": item["call_id"], "at": at}
            elif kind == "function_call_output" and item["call_id"] in tools:
                tools[item["call_id"]].update(result=item["output"], is_error=item.get("is_error", False))
        try:
            userdata = session.userdata
        except ValueError:  # no userdata set
            userdata = None
        release = userdata.get("sopops_release") if isinstance(userdata, dict) else getattr(userdata, "sopops_release", None)
        body = {
            "platform": "livekit", "external_id": ctx.job.id, "agent": agent or ctx.job.agent_name or None, "release": release,
            "started_at": started.isoformat(), "ended_at": datetime.now(timezone.utc).isoformat(),
            "turns": turns, "tool_calls": list(tools.values()), "metadata": {"room": ctx.room.name, "shutdown_reason": reason},
        }
        async with httpx.AsyncClient(timeout=5) as http:  # stay well inside the job's shutdown budget
            res = await http.post(f"{os.environ['SOPOPS_URL']}/sessions", json=body, headers={"Authorization": f"Bearer {os.environ['SOPOPS_INGEST_TOKEN']}"})
            res.raise_for_status()

    ctx.add_shutdown_callback(send)
