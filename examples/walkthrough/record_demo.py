"""Record the demo: open every page of the walkthrough's workspace in a browser and keep each answer the UI got.

The demo build of the UI (`pnpm -C web build:demo`, VITE_SOPOPS_DEMO=1) answers its API calls from what this writes to
web/demo-data/, so it runs as a static site with no sopops behind it (web/src/lib/demo.ts). Run it against a finished
walkthrough served with sign-in off: `just examples/walkthrough/demo` does the whole thing.

It starts at the workspace's pages, follows every link that stays in the workspace, clicks every tab, and opens every
session. Each fetch the page makes is kept under the key lib/demo.ts looks it up by: the method, the path, the query
sorted, and for a request with a body (the previews the editors make) a hash of the body.

Needs Chrome (or Chromium) and playwright: `uv run --with playwright python examples/walkthrough/record_demo.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote_plus, unquote_plus, urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO, URL, WS  # noqa: E402

OUT = REPO / "web" / "demo-data"
PAGES = [
    "",
    "agents",
    "instructions",
    "procedures",
    "groups",
    "overview",
    "changes",
    "releases",
    "sessions",
    "patterns",
    "suggestions",
    "suggestions?status=applied",
    "suggestions?status=dismissed",
    "suggestions?status=all",
    "results",
    "results/releases",
    "results/versions",
    "report",
    "settings",
]
SKIP = (
    "/setup",
    "/convert",
    "/import/",
)  # pages that start something rather than show it
PER_PATH = 6  # filtered variants of one page worth keeping (Results links to many)


def key(method: str, url: str, body: str | None) -> str:
    """The same key as demoKey in web/src/lib/demo.ts."""
    parts = urlsplit(url)
    pairs = sorted(
        (p.split("=", 1) if "=" in p else (p, "") for p in parts.query.split("&") if p),
        key=lambda kv: kv[0],
    )
    query = (
        "?"
        + "&".join(
            f"{quote_plus(_unquote(k), safe='*')}={quote_plus(_unquote(v), safe='*')}"
            for k, v in pairs
        )
        if pairs
        else ""
    )
    digest = f" {hashlib.sha1(body.encode()).hexdigest()[:16]}" if body else ""
    return f"{method} {parts.path}{query}{digest}"


def _unquote(s: str) -> str:
    return unquote_plus(s)


def session_ids() -> list[str]:
    ids: list[str] = []
    cursor = None
    with httpx.Client(base_url=URL, timeout=30) as http:
        while True:
            r = http.get(
                f"/v1/workspaces/{WS}/sessions",
                params={"limit": 100, **({"cursor": cursor} if cursor else {})},
            ).json()
            ids += [s["id"] for s in r["items"]]
            cursor = r.get("next_cursor")
            if not cursor:
                return ids


async def main(workers: int, limit: int) -> None:
    from playwright.async_api import async_playwright

    answers: dict[str, dict] = {}
    base = f"/w/{WS}"
    queue: asyncio.Queue[str] = asyncio.Queue()
    seen: set[str] = set()
    per_path: Counter[str] = Counter()

    def enqueue(href: str) -> None:
        parts = urlsplit(href)
        path = parts.path.rstrip("/") or "/"
        if not (path == base or path.startswith(base + "/")) or any(
            s in path for s in SKIP
        ):
            return
        page = path + (f"?{parts.query}" if parts.query else "")
        if page in seen or len(seen) >= limit or per_path[path] >= PER_PATH:
            return
        seen.add(page)
        per_path[path] += 1
        queue.put_nowait(page)

    for p in PAGES:
        enqueue(f"{base}/{p}" if p else base)
    for sid in session_ids():
        enqueue(f"{base}/sessions/{sid}")
    enqueue("/")

    async def keep(response) -> None:
        req = response.request
        if req.resource_type not in ("fetch", "xhr") or not response.url.startswith(
            URL
        ):
            return
        try:
            body = await response.body()
            data = json.loads(body) if body else None
        except (
            Exception
        ):  # not JSON (a download), or the page moved on before it was read
            return
        k = key(req.method, response.url, req.post_data)
        answers.setdefault(k, {"status": response.status, "data": data})

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome")
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        await ctx.route(
            "**/sessions/stream", lambda route: route.abort()
        )  # the live stream never goes idle
        done = 0

        async def worker() -> None:
            nonlocal done
            page = await ctx.new_page()
            page.on("response", lambda r: asyncio.ensure_future(keep(r)))
            while True:
                path = await queue.get()
                try:
                    await page.goto(
                        URL + path, wait_until="networkidle", timeout=20_000
                    )
                    await page.wait_for_timeout(250)
                    for tab in await page.query_selector_all('[role="tab"]'):
                        try:
                            await tab.click(timeout=2_000)
                            await page.wait_for_load_state(
                                "networkidle", timeout=10_000
                            )
                        except Exception:
                            pass
                    for href in await page.eval_on_selector_all(
                        "a[href]", "as => as.map(a => a.getAttribute('href'))"
                    ):
                        enqueue(href)
                except Exception as e:  # one slow page shouldn't stop the recording
                    print(f"  {path}: {e.__class__.__name__}", file=sys.stderr)
                done += 1
                if done % 50 == 0:
                    print(
                        f"  {done} pages, {len(answers)} answers, {queue.qsize()} to go"
                    )
                queue.task_done()

        tasks = [asyncio.create_task(worker()) for _ in range(workers)]
        await queue.join()
        for t in tasks:
            t.cancel()
        await browser.close()

    write(answers)
    print(f"{done} pages, {len(answers)} answers in {OUT.relative_to(REPO)}")


def write(answers: dict[str, dict]) -> None:
    """One file per answer, and index.json: each key's file, and for a read the first answer for its path, used when a
    page asks with filters the recording never saw."""
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    index: dict[str, dict[str, str]] = {"answers": {}, "paths": {}}
    for k in sorted(
        answers, key=lambda k: ("?" in k, k)
    ):  # unfiltered reads first, so they're the paths' default
        name = hashlib.sha1(k.encode()).hexdigest()[:16] + ".json"
        (OUT / name).write_text(json.dumps(answers[k], separators=(",", ":")))
        index["answers"][k] = name
        method, rest = k.split(" ", 1)
        if method == "GET":
            index["paths"].setdefault(rest.split("?", 1)[0], name)
    (OUT / "index.json").write_text(json.dumps(index, separators=(",", ":")))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=1500, help="most pages to open")
    a = ap.parse_args()
    asyncio.run(main(a.workers, a.limit))
