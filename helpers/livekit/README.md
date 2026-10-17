# LiveKit helpers

**Python:** use the client package instead: [`sdks/python`](../../sdks/python) (`sopops_client.livekit.attach` fetches the instructions, keeps the release in `session.userdata` and posts the session at shutdown). [`python/sopops_livekit.py`](python/sopops_livekit.py) stays as a minimal copy-in example with no dependency but `httpx`.

**Node:** [`node/sopops-livekit.ts`](node/sopops-livekit.ts) is the copy-in helper until the TypeScript client ([`sdks/node`](../../sdks/node)) has a LiveKit adapter. Fetch the prompt with `new Sopops().fetchInstructions(...)` from that client.
