"""Step 5a: backfill. Send the two weeks of calls before sopops (out/backfill.jsonl) in bulk.

    python examples/walkthrough/backfill.py [--concurrency 8]

Each LiveKit job's history goes through the SDK's own converter (`sopops.livekit.from_livekit`, what `attach`
uses at shutdown) and is posted with `SopOps.post_session`, stamped release 1: the onboarding import reproduced the
prompts these calls ran on. Re-running is safe: sessions are keyed by platform and job id.

For a backfill straight into the database instead (no server, no token), write canonical sessions and use
`sopops import FILE.jsonl --workspace walkthrough`; in fake mode, though, only the server has the fake models.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta

from common import API, OUT, client, ingest_token, wait_for_pipeline, wait_until_up
from sopops import SopOps
from sopops.livekit import from_livekit


def canonical(job: dict) -> dict:
    """A generated LiveKit job as the canonical session `attach` would have posted."""
    start = datetime.fromisoformat(job["started_at"])
    t0 = start.timestamp()
    items = [{**i, "created_at": t0 + i["created_at"]} for i in job["items"]]
    turns, tool_calls = from_livekit({"items": items})
    return {
        "platform": "livekit", "external_id": job["job_id"], "channel": "voice", "agent": job["agent_name"],
        "release": job["release"], "started_at": start.isoformat(), "ended_at": (start + timedelta(seconds=job["items"][-1]["created_at"] + 2)).isoformat(),
        "turns": turns, "tool_calls": tool_calls, "metadata": {"room": job["room"], "source": "backfill"},
    }


async def send_all(jobs: list[dict], token: str, concurrency: int) -> dict[str, int]:
    sopops = SopOps(API, token)
    gate = asyncio.Semaphore(concurrency)
    counts = {"created": 0, "updated": 0, "unchanged": 0}

    async def one(job: dict) -> None:
        async with gate:
            got = await sopops.post_session(canonical(job))
        counts["created" if got["created"] else "updated" if got["updated"] else "unchanged"] += 1

    await asyncio.gather(*(one(j) for j in jobs))
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--no-wait", action="store_true", help="Don't wait for the pipeline to check them.")
    args = parser.parse_args()
    wait_until_up()
    jobs = [json.loads(line) for line in (OUT / "backfill.jsonl").read_text().splitlines() if line.strip()]
    with client() as http:
        token = ingest_token(http)
        counts = asyncio.run(send_all(jobs, token, args.concurrency))
        print(", ".join(f"{v} {k}" for k, v in counts.items()))
        if not args.no_wait:
            done = wait_for_pipeline(http, len(jobs))
            print("checked: " + ", ".join(f"{v} {k}" for k, v in sorted(done.items())))


if __name__ == "__main__":
    main()
