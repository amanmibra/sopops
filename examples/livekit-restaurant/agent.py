"""A LiveKit phone agent for a restaurant, with its instructions served by sopops.

One codebase, deployed once per restaurant: each deployment sets AGENT_NAME (tonys-pizza, luigis-trattoria,
sakura-sushi), which is both its LiveKit agent_name and its agent in sopops (`livekit:<AGENT_NAME>`).
"""

import logging
import os

from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli
from sopops_client.livekit import attach

from tools import TOOLS

load_dotenv(".env.local")
log = logging.getLogger("livekit-restaurant")

AGENT_NAME = os.environ.get("AGENT_NAME")
if not AGENT_NAME:
    raise SystemExit("Set AGENT_NAME, e.g. AGENT_NAME=tonys-pizza")

# Used only if sopops can't be reached and this machine has never fetched the prompt before.
FALLBACK = (
    "You are the phone host for a restaurant. Apologize that the ordering system is down, "
    "take the caller's name and phone number, and say a staff member will call back shortly."
)

server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()  # first, so attach sees a release pinned in the room's metadata

    session = AgentSession(
        # Use whatever STT / LLM / TTS you already run; sopops only supplies the instructions.
        stt="assemblyai/universal-streaming:en",
        llm="openai/gpt-4.1-mini",
        tts="cartesia/sonic-2:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
    )

    # The live release's prompt for this restaurant (or the last good copy on disk if sopops is down).
    # When the job ends, the call is posted to sopops with the release it ran on.
    ins = await attach(ctx, session, agent=f"livekit:{AGENT_NAME}", fallback=FALLBACK, stream=os.environ.get("SOPOPS_STREAM") == "1")
    log.info("sopops: %s release %s (%s)", ins.agent, ins.release, ins.source)

    # The SOPs tell the agent to use these tools by name, so the agent must register them.
    await session.start(room=ctx.room, agent=Agent(instructions=ins.text, tools=TOOLS))
    await session.generate_reply(instructions="Greet the caller and ask how you can help.")


if __name__ == "__main__":
    cli.run_app(server)
