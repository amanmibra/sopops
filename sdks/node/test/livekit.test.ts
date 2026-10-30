import { describe, expect, it, vi } from "vitest";
import { MemoryCache, SopOps } from "../src/index.js";
import { agentConfig, attach, fromLiveKit, pinnedRelease, recordSession } from "../src/livekit.js";

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

const AGENT = { agent: "tonys-pizza", platform_ref: "livekit:tonys-pizza", release: 3, hash: "abc", prompt: "You are Tony's.", tools: [], tool_sops: [], components: [] };

/** A fetch that answers every GET with `agent` and every POST with an ingested session, recording what it was sent. */
function mockFetch(agent: Record<string, unknown> = AGENT) {
  const calls: Array<{ method: string; url: string; body: any }> = [];
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = new Request(input, init);
    const body = req.method === "POST" ? await req.json() : null;
    calls.push({ method: req.method, url: req.url, body });
    return req.method === "POST"
      ? new Response(JSON.stringify({ id: "s1", created: true }), { status: 201 })
      : new Response(JSON.stringify(agent), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof globalThis.fetch;
  return { calls, client: new SopOps({ url: URL, token: "tok", fetch, cache: new MemoryCache() }) };
}

function jobContext(metadata: { job?: string; room?: string } = {}) {
  const callbacks: Array<(reason?: string) => Promise<void>> = [];
  return {
    job: { id: "AJ_1", agentName: "tonys-pizza", metadata: metadata.job ?? "" },
    room: { name: "room-1", metadata: metadata.room ?? "" },
    addShutdownCallback: (cb: (reason?: string) => Promise<void>) => callbacks.push(cb),
    callbacks,
  };
}

const session = { history: { toJSON: () => HISTORY } };

describe("attach", () => {
  it("fetches the instructions and posts at shutdown with the agent and release", async () => {
    const { calls, client } = mockFetch();
    const ctx = jobContext();

    const ins = await attach(ctx, session, { sopops: client });
    expect(ins.text).toBe("You are Tony's.");
    expect(ins.release).toBe(3);
    expect(calls[0]!.url).toBe(`${URL}/agents/tonys-pizza?platform=livekit`); // the job's agent name, no release pinned
    expect(ctx.callbacks).toHaveLength(1);

    await ctx.callbacks[0]!();
    expect(calls[1]!.url).toBe(`${URL}/sessions`);
    expect(calls[1]!.body).toMatchObject({ platform: "livekit", external_id: "AJ_1", agent: "tonys-pizza", release: 3, metadata: { room: "room-1" } });
    expect(calls[1]!.body.turns).toHaveLength(4);
  });

  it("fills variables in this process and never sends them to sopops", async () => {
    const { calls, client } = mockFetch({ ...AGENT, prompt: "You are Tony's. The caller is {{caller}} on {{plan}}." });
    const ctx = jobContext();

    const ins = await attach(ctx, session, { sopops: client, variables: { caller: "Ada Lovelace" } });
    await ctx.callbacks[0]!();
    expect(ins.text).toBe("You are Tony's. The caller is Ada Lovelace on {{plan}}.");
    expect(ins.unfilled).toEqual(["plan"]);
    expect(JSON.stringify(calls)).not.toContain("Ada Lovelace");
  });

  it("pins the fetch to an explicit release, over one in metadata", async () => {
    const { calls, client } = mockFetch({ ...AGENT, release: 7 });
    const ctx = jobContext({ job: '{"sopops_release": 2}' });

    const ins = await attach(ctx, session, { sopops: client, release: 7 });
    await ctx.callbacks[0]!();
    expect(calls[0]!.url).toBe(`${URL}/agents/tonys-pizza?release=7&platform=livekit`);
    expect(ins.release).toBe(7);
    expect(calls[1]!.body.release).toBe(7);
  });

  it("honors a release pinned in the job's, then the room's, metadata", async () => {
    const job = mockFetch({ ...AGENT, release: 2 });
    await attach(jobContext({ job: '{"sopops_release": 2}', room: '{"sopops": {"release": 1}}' }), session, { sopops: job.client });
    expect(job.calls[0]!.url).toBe(`${URL}/agents/tonys-pizza?release=2&platform=livekit`); // the job's pin wins

    const room = mockFetch({ ...AGENT, release: 1 });
    const ins = await attach(jobContext({ room: '{"sopops": {"release": 1}}' }), session, { sopops: room.client });
    expect(room.calls[0]!.url).toBe(`${URL}/agents/tonys-pizza?release=1&platform=livekit`);
    expect(ins.release).toBe(1);
  });
});

/** Like `voice.AgentSession`: reading `userData` before it's set throws, and `on(event, listener)` with `emit` for the test. */
class StreamingSession {
  history = { toJSON: () => HISTORY };
  handlers: Record<string, Array<(ev: any) => void>> = {};
  private data: unknown;
  get userData(): unknown {
    if (this.data === undefined) throw new Error("VoiceSession userData is not set");
    return this.data;
  }
  set userData(value: unknown) {
    this.data = value;
  }
  on(event: string, listener: (ev: any) => void) {
    (this.handlers[event] ??= []).push(listener);
  }
  emit(event: string, ev: unknown) {
    for (const h of this.handlers[event] ?? []) h(ev);
  }
}

const said = (role: string, text: string, createdAt: number, interrupted = false) => ({ item: { type: "message", role, content: [text], createdAt, interrupted } });
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("attach with stream", () => {
  it("notes the release in userData and posts the shutdown reason, sending nothing until shutdown", async () => {
    const { calls, client } = mockFetch();
    const ctx = jobContext();
    const s = new StreamingSession();
    await attach(ctx, s, { sopops: client });
    expect(s.userData).toEqual({ sopops_release: 3, sopops_agent: "tonys-pizza" });
    expect(s.handlers).toEqual({});
    await ctx.callbacks[0]!("user_left");
    expect(calls.map((c) => c.url)).toEqual([`${URL}/agents/tonys-pizza?platform=livekit`, `${URL}/sessions`]);
    expect(calls[1]!.body.metadata).toEqual({ room: "room-1", shutdown_reason: "user_left" });
  });

  it("streams turns and tool calls as they happen, then posts the full session", async () => {
    const { calls, client } = mockFetch();
    const ctx = jobContext();
    const s = new StreamingSession();
    const guidance = vi.fn();
    await attach(ctx, s, { sopops: client, stream: true, test: "t1", tags: ["legacy"], onGuidance: guidance });
    s.emit("conversation_item_added", said("assistant", "Luigi's, how can I help?", 100_000));
    s.emit("conversation_item_added", said("user", "A table for six.", 104_000));
    s.emit("conversation_item_added", said("user", "A table for six.", 104_100)); // a repeat: dropped
    s.emit("function_tools_executed", {
      functionCalls: [{ name: "check_reservations", args: '{"party_size": 6}', callId: "c1", createdAt: 106_000 }],
      functionCallOutputs: [{ output: '{"available": true}', isError: false }],
    });
    s.emit("conversation_item_added", { item: undefined }); // malformed: logged
    await wait(600); // one flush interval
    await ctx.callbacks[0]!("done");

    const open = calls.filter((c) => c.url === `${URL}/sessions/open`);
    expect(open).toHaveLength(1);
    expect(open[0]!.body).toMatchObject({ platform: "livekit", external_id: "AJ_1", channel: "voice", agent: "tonys-pizza", release: 3, test: "t1", tags: ["legacy"], metadata: { room: "room-1" } });
    const sent = calls.filter((c) => c.url === `${URL}/sessions/livekit/AJ_1/events`).flatMap((c) => c.body.events);
    expect(sent.map((e: any) => [e.seq, e.type])).toEqual([[0, "turn"], [1, "turn"], [2, "turn"], [3, "tool_call"]]);
    expect(sent[3].tool_call).toEqual({ name: "check_reservations", arguments: '{"party_size": 6}', call_id: "c1", at: 106_000, result: '{"available": true}', is_error: false });
    const full = calls.filter((c) => c.url === `${URL}/sessions`);
    expect(full).toHaveLength(1);
    expect(full[0]!.body).toMatchObject({ external_id: "AJ_1", release: 3, test: "t1", tags: ["legacy"], metadata: { shutdown_reason: "done" } });
    expect(guidance).not.toHaveBeenCalled(); // reserved
  });
});

describe("recordSession defaults", () => {
  it("reads the release attach noted in userData", async () => {
    const { calls, client } = mockFetch();
    const ctx = jobContext();
    const s = new StreamingSession();
    s.userData = { sopops_release: 9, sopops_agent: "tonys-pizza" };
    recordSession(ctx, s, { sopops: client });
    await ctx.callbacks[0]!();
    expect(calls[0]!.body).toMatchObject({ agent: "tonys-pizza", release: 9 });
  });

  it("builds its own client from the environment", async () => {
    vi.stubEnv("SOPOPS_URL", URL);
    try {
      expect(() => recordSession(jobContext(), session)).not.toThrow();
    } finally {
      vi.unstubAllEnvs();
    }
  });
});

describe("agentConfig and pinnedRelease", () => {
  it("names a provider by its company when a plugin reports its API host", () => {
    expect(agentConfig({ ...session, llm: { provider: "api.anthropic.com", model: "claude-haiku-4-5" }, stt: { provider: "deepgram", model: "nova-3" } })).toEqual({
      llm: "anthropic/claude-haiku-4-5",
      stt: "deepgram/nova-3",
    });
  });

  it("counts a pin of release 0", () => {
    expect(pinnedRelease(jobContext({ job: '{"sopops_release": 0}', room: '{"sopops_release": 4}' }))).toBe(0);
  });
});
