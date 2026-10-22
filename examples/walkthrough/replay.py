"""Step 7: replay new calls through the Python SDK's LiveKit adapter, as if they were happening now.

    python examples/walkthrough/replay.py [--rate 2] [--speed 30] [--concurrency 10] [--limit N]

For each job in out/new.jsonl this does what agent/after/agent.py does in a real LiveKit worker, with stand-ins for
LiveKit's `JobContext` and `AgentSession`:

1. `attach(ctx, session, agent="livekit:<agent>", stream=True)` fetches the live release's prompt, notes the release
   in `session.userdata`, and opens the session in sopops (`…/sessions/open`).
2. Each message and tool call is emitted as LiveKit emits it (`conversation_item_added`, `function_tools_executed`),
   and the adapter streams it (`…/sessions/livekit/<job id>/events`), so the call shows on the Sessions page while
   it's going.
3. At shutdown the adapter posts the full history with the release, which closes and checks the session.

`--rate` new calls start per second; `--speed` plays each call that many times faster than real time.
The calls were generated for the prompt after the fix, so publish release 2 first (review.py does).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from common import API, OUT, client, ingest_token, wait_for_pipeline, wait_until_up
from sopops import SopOps
from sopops.livekit import attach


class History:
    """`session.history`: a ChatContext whose `to_dict` gives its items."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def to_dict(self, exclude_timestamp: bool = True) -> dict[str, Any]:
        return {"items": [i if not exclude_timestamp else {k: v for k, v in i.items() if k != "created_at"} for i in self.items]}


class AgentSession:
    """The parts of `livekit.agents.AgentSession` the adapter uses: `userdata`, `history` and `on(event, handler)`."""

    def __init__(self) -> None:
        self.history = History()
        self.handlers: dict[str, list[Callable[[Any], None]]] = {}
        self._userdata: Any = None

    @property
    def userdata(self) -> Any:
        if self._userdata is None:
            raise ValueError("VoiceAgent userdata is not set")  # as LiveKit does
        return self._userdata

    @userdata.setter
    def userdata(self, value: Any) -> None:
        self._userdata = value

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, payload: Any) -> None:
        for h in self.handlers.get(event, []):
            h(payload)


class JobContext:
    """The parts of `livekit.agents.JobContext` the adapter uses."""

    def __init__(self, job_id: str, agent_name: str, room: str) -> None:
        self.job = SimpleNamespace(id=job_id, agent_name=agent_name, metadata="")
        self.room = SimpleNamespace(name=room, metadata="")
        self.callbacks: list[Callable[[str], Awaitable[None]]] = []

    def add_shutdown_callback(self, cb: Callable[[str], Awaitable[None]]) -> None:
        self.callbacks.append(cb)

    async def shutdown(self, reason: str) -> None:
        for cb in self.callbacks:
            await cb(reason)


async def play(job: dict[str, Any], sopops: SopOps, speed: float) -> int | None:
    """One call, start to shutdown. Returns the release it ran on."""
    ctx, session = JobContext(job["job_id"], job["agent_name"], job["room"]), AgentSession()
    ins = await attach(ctx, session, agent=f"livekit:{job['agent_name']}", client=sopops, stream=True)
    t0, last = time.time(), 0.0
    items = job["items"]
    i = 0
    while i < len(items):
        item = {**items[i], "created_at": t0 + items[i]["created_at"]}
        await asyncio.sleep(max(items[i]["created_at"] - last, 0) / speed)
        last = items[i]["created_at"]
        if item["type"] == "message":
            session.history.items.append(item)
            session.emit("conversation_item_added", SimpleNamespace(item=SimpleNamespace(**item)))
            i += 1
            continue
        output = {**items[i + 1], "created_at": t0 + items[i + 1]["created_at"]}
        session.history.items += [item, output]
        session.emit("function_tools_executed", SimpleNamespace(
            function_calls=[SimpleNamespace(name=item["name"], arguments=item["arguments"], call_id=item["call_id"], created_at=item["created_at"])],
            function_call_outputs=[SimpleNamespace(output=output["output"], is_error=output["is_error"])],
        ))
        i += 2
    await ctx.shutdown("participant left")
    return ins.release


async def replay(jobs: list[dict[str, Any]], token: str, rate: float, speed: float, concurrency: int) -> Counter[int | None]:
    sopops = SopOps(API, token, cache_dir=OUT / "cache")
    gate = asyncio.Semaphore(concurrency)
    releases: Counter[int | None] = Counter()

    async def one(job: dict[str, Any]) -> None:
        async with gate:
            releases[await play(job, sopops, speed)] += 1
            done = sum(releases.values())
            if done % 10 == 0 or done == len(jobs):
                print(f"  {done}/{len(jobs)} calls replayed", flush=True)

    tasks = []
    for job in jobs:
        tasks.append(asyncio.create_task(one(job)))
        await asyncio.sleep(1 / rate)
    await asyncio.gather(*tasks)
    return releases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rate", type=float, default=2.0, help="New calls per second.")
    parser.add_argument("--speed", type=float, default=30.0, help="How many times faster than real time each call plays.")
    parser.add_argument("--concurrency", type=int, default=10, help="Calls in progress at once, at most.")
    parser.add_argument("--limit", type=int, help="Only the first N calls.")
    parser.add_argument("--no-wait", action="store_true", help="Don't wait for the pipeline to check them.")
    args = parser.parse_args()
    wait_until_up()
    jobs = [json.loads(line) for line in (OUT / "new.jsonl").read_text().splitlines() if line.strip()][: args.limit]
    with client() as http:
        token = ingest_token(http)
        releases = asyncio.run(replay(jobs, token, args.rate, args.speed, args.concurrency))
        print("ran on " + ", ".join(f"release {r}: {n}" for r, n in sorted(releases.items(), key=lambda kv: kv[0] or 0)))
        if 1 in releases:
            print("Some calls ran on release 1: publish release 2 first (python examples/walkthrough/review.py).")
        if not args.no_wait:
            done = wait_for_pipeline(http, sum(1 for _ in (OUT / "backfill.jsonl").open()) + len(jobs))
            print("checked: " + ", ".join(f"{v} {k}" for k, v in sorted(done.items())))


if __name__ == "__main__":
    main()
