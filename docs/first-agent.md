# Run a self-hosted demo that ships a fix

Run a self-hosted demo with four fictional restaurant agents. You start sopops
on your machine, import prompts, check calls, and publish a fix. Scripted
models find the mistakes planted in its calls, so the demo has no model cost.

This tutorial does not set up app.sopops.dev. You run the server, its database,
and sign-in yourself.

## The run produces a measured release

1. Import four prompts and publish release 1.
2. Fetch that release when a LiveKit call starts.
3. Check 200 calls and group their mistakes.
4. Review two findings and publish release 2.
5. Replay 100 calls and compare the releases.

The demo uses the LiveKit SDK. Your own agents can send calls back with no
code; see [Connect your agent](connect.md).

Before you begin, install Git, Python, Node, [just](https://github.com/casey/just),
and [sopc](https://github.com/amanmibra/sopc) v0.0.18 or later. On macOS, run
`brew install just` to install just.

## Start a local server with no keys

Clone sopops, build its UI once, then run the tutorial. This command starts a
fresh server on `http://127.0.0.1:8585`, uses a new database, and leaves it
running when it finishes.

```sh
git clone https://github.com/amanmibra/sopops
cd sopops
pnpm -C web install && pnpm -C web build
just examples/walkthrough/all
```

The run prints the import result and the first release:

```text
bayleaf-bistro: fidelity 100%
juniper-grill: fidelity 100%
copper-wok-eastside: fidelity 100%
copper-wok-harbor: fidelity 100%
Resolved '...': everyone gets bayleaf-bistro's wording.
Published release 1.
```

While the tutorial runs, open `http://127.0.0.1:8585/w/walkthrough/import`.
The sample prompts begin in `examples/walkthrough/agent/before/prompts.py`. The
import turns shared text into three shared instructions and three procedures.
It finds an older allergen rule in the Copper Wok prompts, keeps Bayleaf
Bistro's wording, writes drafts, and publishes release 1.

## A LiveKit agent fetches its release at call start

After you create a `LiveKit` session and define `TOOLS`, add this code to your
entrypoint. This code is in `examples/walkthrough/agent/after/agent.py`.

```python
from sopops.livekit import attach

FALLBACK = (
    "You are the phone host for a restaurant. Apologize that the ordering system "
    "is down, take the caller's name and phone number, and say a staff member will "
    "call back shortly."
)

ins = await attach(
    ctx,
    session,
    agent=AGENT_NAME,
    fallback=FALLBACK,
    stream=True,
)
await session.start(room=ctx.room, agent=Agent(instructions=ins.text, tools=TOOLS))
```

Pass `agent` as the agent's platform identifier. Pass `fallback` as the prompt
to use when no saved instruction is available. Set `stream=True` to send turns
and tool calls while the call runs.

`attach` returns `ins`; pass `ins.text` to `Agent`. It records the release on
the session and posts the full call at shutdown. If sopops is unavailable, it
uses a saved instruction, then `FALLBACK`. A real agent also needs
`pip install "sopops[livekit]"`, `SOPOPS_URL`, and a token with the `runtime`
and `ingest` scopes, set where the agent runs: on LiveKit Cloud, as the agent's
secrets. If neither a saved instruction nor `FALLBACK` exists,
`attach` raises `SopOpsUnavailable`.

The walkthrough drives the same adapter with LiveKit stand-ins. Its output
confirms that it sent and checked 200 earlier calls against release 1:

```text
200 created
checked: 200 checked
```

Open `http://127.0.0.1:8585/w/walkthrough/results`. **Overview** shows the
measured result first. Use it to find a change that needs attention or a
release worth comparing.

![The Results overview shows the checked sessions, pass rate, notable changes,
and the procedures behind them.](images/walkthrough-results-overview.png)

Open **Review report** in the Results sidebar for the evidence. It groups the
findings by procedure and shows the sessions, rule, and quote that produced
each one. In **Patterns**, one allergen rule separates into failures such as a
promise that a dish is safe and a claim that a kitchen is allergen-free.

![The Review report groups checked calls by procedure and shows the count of
sessions that followed, partially followed, or deviated from each one.](images/walkthrough-review-report.png)

## A review turns two findings into release 2

The run gives each pattern a verdict. It marks the false alarm as checker wrong,
suppresses the delivery-time finding as acceptable, and keeps the real mistakes.
It asks for an edit on two patterns:

```text
safe_claim: 'Told the caller a dish is safe' (...) -> real + suggestion
eta: 'Gave the caller a delivery time' (...) -> acceptable + suggestion
suggestion for safe_claim: applied
suggestion for eta: applied
Published release 2.
```

Open `http://127.0.0.1:8585/w/walkthrough/suggestions`. One edit adds guidance
for a caller asking whether a dish is safe. The other narrows a bad rule from
“Never tell the caller a delivery time” to “Never tell the caller a delivery
time before the delivery zone check confirms the address.” Both compile before
they become drafts. With a GitHub connection, **Suggestions** opens pull
requests instead.

## The next calls show whether the release helped

After release 2, the tutorial replays 100 new calls through `attach`. They
fetch release 2 at call start, so you do not need to deploy the agent again.
The final checklist confirms the run:

```text
PASS  backfill: 200 of 200 sessions ingested
PASS  every session checked
PASS  backfill: stamped release 1
PASS  new: stamped release 2 (fetched by the SDK at call start)
PASS  allergen-check (the fix): pass rate 53% (...) -> 85% (...)
```

Open `http://127.0.0.1:8585/w/walkthrough/results/releases` to compare
releases. The allergen procedure improves on calls that ran the new
instructions. The delivery rule keeps its history across the rewording, while
its acceptable findings stay out of the pass rate.

![The walkthrough release comparison shows release 1 and release 2 beside a
pass-rate chart and a procedure-by-procedure comparison.](images/walkthrough-release-compare.png)

**A/B tests** in the Results sidebar is for intentional split traffic. The demo
does not split calls, but this view lets you choose versions of the same agent
before you compare them.

![The A/B tests view lists versions you can select for a controlled
comparison.](images/walkthrough-ab-tests.png)

Stop the server with Ctrl-C when you finish. The tutorial stores data in
`examples/walkthrough/out/`; run `just examples/walkthrough/clean` to remove it.

To view the finished demo behind local sign-in, run
`just examples/walkthrough/all-sign-in`. It creates `test@example.com` with
password `password123` after the tutorial finishes. Do not use that account or
password outside this local demo.

For SDK types and functions, see the [SDK reference](sdk.md). For server
settings, see [configuration](configuration.md). For endpoints, see the
[API reference](api/).
