import { describe, expect, it, vi } from "vitest";
import { MemoryCache, SopOps, type SessionEvent } from "../src/index.js";
import { elevenLabsSession } from "../src/elevenlabs.js";

const URL = "http://sopops.test/v1/workspaces/prod";
const AGENT = { agent: "sakura-sushi", platform_ref: "elevenlabs:agent_123", release: 3, hash: "abc", prompt: "You are Sakura's host.", tools: [], tool_sops: [], components: [] };

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

type Reply = Response | Error;

/** A fake sopops, answering by path; each route replies with its list in turn, repeating the last. */
function fakeSopops(routes: Record<string, Reply[]>) {
  const calls: Array<{ path: string; body: any; auth: string | null }> = [];
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = new Request(input, init);
    const path = req.url.slice(URL.length);
    calls.push({ path, body: req.method === "POST" ? await req.json() : null, auth: req.headers.get("Authorization") });
    const replies = routes[path];
    if (!replies) return json({ detail: "not found" }, 404);
    const next = replies.length > 1 ? replies.shift()! : replies[0]!;
    if (next instanceof Error) throw next;
    return next.clone();
  }) as unknown as typeof globalThis.fetch;
  const to = (path: string) => calls.filter((c) => c.path === path);
  const events = (path: string): SessionEvent[] => to(path).flatMap((c) => c.body.events);
  return { client: new SopOps({ url: URL, token: "tok", fetch, cache: new MemoryCache() }), calls, to, events };
}

const OPENED = json({ id: "s1", created: true }, 201);
const ACCEPTED = json({ accepted: 1, duplicates: 0 });
const INGESTED = json({ id: "s1", created: true, updated: false, status: "received", warnings: [] }, 201);
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("SessionStream", () => {
  it("opens the session, then sends events in batches with seq from 0", async () => {
    const f = fakeSopops({ "/sessions/open": [OPENED], "/sessions/api/web-chat-1/events": [ACCEPTED] });
    const stream = f.client.stream({ platform: "api", external_id: "web-chat-1", agent: "tonys-pizza", release: 3, version_ref: null }, { flushMs: 10 });
    stream.turn({ role: "user", text: "Delivery please." });
    stream.turn({ role: "agent", text: "What's the address?" });
    stream.toolCall({ name: "check_delivery_zone", arguments: { address: "221 Mercer St" }, result: { in_zone: true } });
    expect(f.calls).toHaveLength(0); // nothing blocks the caller
    await vi.waitFor(() => expect(f.to("/sessions/api/web-chat-1/events")).toHaveLength(1));
    stream.turn({ role: "agent", text: "You're in the zone." });
    await stream.close();

    expect(f.to("/sessions/open").map((c) => c.body)).toEqual([{ platform: "api", external_id: "web-chat-1", agent: "tonys-pizza", release: 3 }]);
    expect(f.to("/sessions/open")[0]!.auth).toBe("Bearer tok");
    const sent = f.events("/sessions/api/web-chat-1/events");
    expect(sent.map((e) => [e.seq, e.type])).toEqual([[0, "turn"], [1, "turn"], [2, "tool_call"], [3, "turn"]]);
    expect(sent[2]).toEqual({ seq: 2, type: "tool_call", tool_call: { name: "check_delivery_zone", arguments: { address: "221 Mercer St" }, result: { in_zone: true } } });
    expect(f.to("/sessions/api/web-chat-1/events")).toHaveLength(2);
    expect(stream.sent).toBe(4);
  });

  it("keeps batches and the queue bounded", async () => {
    const f = fakeSopops({ "/sessions/open": [OPENED], "/sessions/api/x/events": [ACCEPTED] });
    const stream = f.client.stream({ platform: "api", external_id: "x" }, { flushMs: 60_000, maxBatch: 2, maxQueue: 3 });
    for (let i = 0; i < 5; i++) stream.turn({ role: "user", text: String(i) }); // the queue fills before anything is sent
    await stream.close();
    expect(f.to("/sessions/api/x/events").map((c) => c.body.events.length)).toEqual([2, 1]);
    expect(f.events("/sessions/api/x/events").map((e) => e.seq)).toEqual([0, 1, 2]);
    expect([stream.sent, stream.dropped]).toEqual([3, 2]);
  });

  it("never throws when sopops fails, and tries to open again", async () => {
    const f = fakeSopops({ "/sessions/open": [json({}, 503), OPENED], "/sessions/api/x/events": [new TypeError("fetch failed")] });
    const stream = f.client.stream({ platform: "api", external_id: "x" }, { flushMs: 5 });
    stream.turn({ role: "user", text: "Hi" });
    await wait(30);
    stream.turn({ role: "agent", text: "Hello!" });
    await expect(stream.close()).resolves.toBeUndefined();
    expect(f.to("/sessions/open")).toHaveLength(2);
    expect(f.events("/sessions/api/x/events").map((e) => e.seq)).toEqual([1]);
    expect([stream.sent, stream.dropped]).toEqual([0, 2]);
    stream.turn({ role: "user", text: "after close" }); // ignored
    expect(f.calls).toHaveLength(3);
  });
});

describe("elevenLabsSession with stream", () => {
  it("adds no handlers without stream", async () => {
    const f = fakeSopops({ "/agents/sakura-sushi?platform=elevenlabs": [json(AGENT)] });
    const s = await elevenLabsSession(f.client, { agent: "sakura-sushi" });
    expect(Object.keys(s.sessionOptions).sort()).toEqual(["agentId", "dynamicVariables", "onConnect", "overrides"]);
    await s.end();
    expect(f.calls).toHaveLength(1);
  });

  it("streams messages and tool results, then posts the full conversation", async () => {
    const f = fakeSopops({
      "/agents/sakura-sushi?platform=elevenlabs": [json(AGENT)],
      "/runtime/served": [json({})],
      "/sessions/open": [OPENED],
      "/sessions/elevenlabs/conv_1/events": [ACCEPTED],
      "/sessions": [INGESTED],
    });
    const guidance = vi.fn();
    const s = await elevenLabsSession(f.client, { agent: "sakura-sushi", stream: true, onGuidance: guidance });
    const o = s.sessionOptions;
    o.onMessage!({ message: "Sakura Sushi, how can I help?", role: "agent" }); // before the conversation id is known
    o.onConnect({ conversationId: "conv_1" });
    o.onMessage!({ message: "A table for four tonight?", role: "user" });
    o.onAgentToolResponse!({ tool_name: "check_reservations", tool_call_id: "t1", is_error: false, full_tool_result: '{"available": true}' });
    o.onDisconnect!({ reason: "user" });
    await s.end();

    expect(f.to("/sessions/open")[0]!.body).toMatchObject({ platform: "elevenlabs", external_id: "conv_1", channel: "voice", agent: "sakura-sushi", release: 3 });
    const sent = f.events("/sessions/elevenlabs/conv_1/events");
    expect(sent.map((e) => [e.seq, e.type])).toEqual([[0, "turn"], [1, "turn"], [2, "turn"], [3, "tool_call"]]);
    expect(sent[3]).toMatchObject({ tool_call: { name: "check_reservations", call_id: "t1", is_error: false, result: '{"available": true}' } });

    const full = f.to("/sessions");
    expect(full).toHaveLength(1);
    expect(full[0]!.body).toMatchObject({ platform: "elevenlabs", external_id: "conv_1", agent: "sakura-sushi", release: 3, metadata: { disconnect_reason: "user" } });
    expect(full[0]!.body.turns.map((t: { role: string }) => t.role)).toEqual(["agent", "user", "tool"]);
    expect(full[0]!.body.tool_calls).toHaveLength(1);
    expect(f.to("/runtime/served")).toHaveLength(1);
    expect(guidance).not.toHaveBeenCalled(); // reserved
  });

  it("never throws when the final post fails", async () => {
    const f = fakeSopops({ "/agents/sakura-sushi?platform=elevenlabs": [json(AGENT)], "/runtime/served": [json({})], "/sessions/open": [OPENED], "/sessions/elevenlabs/conv_2/events": [ACCEPTED], "/sessions": [json({ detail: "invalid ingest token" }, 401)] });
    const s = await elevenLabsSession(f.client, { agent: "sakura-sushi", stream: true });
    s.sessionOptions.onConnect({ conversationId: "conv_2" });
    s.sessionOptions.onMessage!({ message: "Hi", role: "user" });
    await expect(s.end("agent")).resolves.toBeUndefined();
    expect(f.to("/sessions")).toHaveLength(1);
  });
});
