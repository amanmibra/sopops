import { describe, expect, it, vi } from "vitest";
import { MemoryCache, SopOps } from "../src/index.js";
import { fromLiveKit, recordSession } from "../src/livekit.js";

const URL = "http://sopops.test/v1/workspaces/prod";

// What `session.history.toJSON({ excludeTimestamp: false })` returns in @livekit/agents (Node): camelCase, milliseconds.
const HISTORY = {
  items: [
    { type: "message", role: "assistant", content: ["Luigi's Trattoria, how can I help?"], createdAt: 100_000 },
    { type: "message", role: "user", content: ["A table for six on Saturday."], createdAt: 104_000 },
    { type: "message", role: "user", content: ["A table for six on Saturday."], createdAt: 104_100 },
    { type: "function_call", callId: "c1", name: "check_reservations", args: '{"party_size": 6}', createdAt: 106_000 },
    { type: "function_call_output", callId: "c1", name: "check_reservations", output: '{"available": true}', isError: false, createdAt: 106_500 },
    { type: "message", role: "assistant", content: ["You're booked.", { audio: "..." }], interrupted: true, createdAt: 108_000 },
  ],
};

describe("fromLiveKit", () => {
  it("maps turns and tool calls, dropping a repeated message", () => {
    const { turns, toolCalls } = fromLiveKit(HISTORY);
    expect(turns.map((t) => t.role)).toEqual(["assistant", "user", "tool", "assistant"]);
    expect(turns[1]).toEqual({ role: "user", text: "A table for six on Saturday.", at: 104_000, interrupted: false });
    expect(turns[3]).toMatchObject({ text: "You're booked.", interrupted: true });
    expect(toolCalls).toEqual([{ name: "check_reservations", arguments: '{"party_size": 6}', call_id: "c1", at: 106_000, result: '{"available": true}', is_error: false }]);
  });
});

describe("recordSession", () => {
  it("posts the session at shutdown, keyed by the job id", async () => {
    const bodies: any[] = [];
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const req = new Request(input, init);
      bodies.push({ url: req.url, body: await req.json() });
      return new Response(JSON.stringify({ id: "s1", created: true }), { status: 201 });
    }) as unknown as typeof globalThis.fetch;
    const sopops = new SopOps({ url: URL, token: "tok", fetch, cache: new MemoryCache() });
    const callbacks: Array<() => Promise<void>> = [];
    const ctx = { job: { id: "AJ_1", agentName: "luigis-trattoria" }, room: { name: "room-1" }, addShutdownCallback: (cb: () => Promise<void>) => callbacks.push(cb) };

    recordSession(ctx, { history: { toJSON: () => HISTORY } }, { sopops, release: 3 });
    await callbacks[0]!();
    expect(bodies[0].url).toBe(`${URL}/sessions`);
    expect(bodies[0].body).toMatchObject({ platform: "livekit", external_id: "AJ_1", agent: "luigis-trattoria", release: 3, metadata: { room: "room-1" } });
    expect(bodies[0].body.turns).toHaveLength(4);
  });

  it("never throws into shutdown", async () => {
    const fetch = vi.fn(async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof globalThis.fetch;
    const sopops = new SopOps({ url: URL, fetch, cache: new MemoryCache() });
    const callbacks: Array<() => Promise<void>> = [];
    const ctx = { job: { id: "AJ_2" }, room: {}, addShutdownCallback: (cb: () => Promise<void>) => callbacks.push(cb) };
    recordSession(ctx, { history: { toJSON: () => HISTORY } }, { sopops });
    await expect(callbacks[0]!()).resolves.toBeUndefined();
  });
});

