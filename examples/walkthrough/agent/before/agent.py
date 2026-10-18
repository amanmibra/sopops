"""Harbor Lane's LiveKit phone host: one codebase, deployed once per restaurant (AGENT_NAME).

The instructions live in prompts.py and ship with the code.
"""

import logging
import os

from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli

from prompts import build_prompt
from tools import TOOLS

load_dotenv(".env.local")
log = logging.getLogger("harbor-lane")

AGENT_NAME = os.environ.get("AGENT_NAME")
if not AGENT_NAME:
    raise SystemExit("Set AGENT_NAME, e.g. AGENT_NAME=juniper-grill")

INSTRUCTIONS = build_prompt(AGENT_NAME)

server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    session = AgentSession(
        stt="assemblyai/universal-streaming:en",
        llm="openai/gpt-4.1-mini",
        tts="cartesia/sonic-2:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
    )

    await session.start(room=ctx.room, agent=Agent(instructions=INSTRUCTIONS, tools=TOOLS))
    await session.generate_reply(instructions="Greet the caller and ask how you can help.")


if __name__ == "__main__":
    cli.run_app(server)
