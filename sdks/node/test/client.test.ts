import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { MemoryCache, Sopops, SopopsError, SopopsUnavailable, releasePin } from "../src/index.js";
import { elevenLabsSession } from "../src/elevenlabs.js";
import { FileCache } from "../src/node.js";

const URL = "http://sopops.test/v1/workspaces/prod";
const AGENT = {
  agent: "front-desk",
  platform_ref: "elevenlabs:agent_123",
  release: 3,
  hash: "abc",
  prompt: "You are the front desk.",
  tools: ["lookup"],
  tool_sops: [],
  components: [{ kind: "agent", id: "front-desk", path: "agents/front-desk.yaml", version: 2 }],
};

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function mockFetch(...responses: Array<Response | Error | ((req: Request) => Promise<Response>)>) {
  const calls: Request[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = new Request(input, init);
    calls.push(req);
    const next = responses.length > 1 ? responses.shift()! : responses[0]!;
    if (next instanceof Error) throw next;
    if (typeof next === "function") return next(req);
    return next.clone();
  });
  return { fetch: fn as unknown as typeof fetch, calls };
}

const client = (f: typeof fetch, extra = {}) => new Sopops({ url: URL, token: "tok", fetch: f, cache: new MemoryCache(), ...extra });

describe("fetchInstructions", () => {
  it("returns the prompt with its release and blocks", async () => {
    const m = mockFetch(json(AGENT));
    const ins = await client(m.fetch).fetchInstructions("front-desk");
    expect(ins).toMatchObject({ text: "You are the front desk.", release: 3, agent: "front-desk", platformRef: "elevenlabs:agent_123", source: "live" });
    expect(ins.blocks[0]?.id).toBe("front-desk");
    expect(m.calls[0]!.url).toBe(`${URL}/agents/front-desk`);
    expect(m.calls[0]!.headers.get("Authorization")).toBe("Bearer tok");
  });

  it("pins a release", async () => {
    const m = mockFetch(json(AGENT));
    await client(m.fetch).fetchInstructions("front-desk", { release: 2 });
    expect(m.calls[0]!.url).toBe(`${URL}/agents/front-desk?release=2`);
  });

  it("uses the last good copy when sopops fails, then the fallback, then throws", async () => {
    const cache = new MemoryCache();
    await client(mockFetch(json(AGENT)).fetch, { cache }).fetchInstructions("front-desk");
    const down = mockFetch(new TypeError("fetch failed"));
    const cached = await client(down.fetch, { cache }).fetchInstructions("front-desk");
    expect(cached).toMatchObject({ source: "cache", release: 3, text: "You are the front desk." });
    expect((await client(mockFetch(json({ detail: "nope" }, 503)).fetch, { cache }).fetchInstructions("front-desk")).source).toBe("cache");

    await expect(client(down.fetch).fetchInstructions("front-desk", { release: 1 })).rejects.toBeInstanceOf(SopopsUnavailable);
    const fb = await client(down.fetch).fetchInstructions("front-desk", { fallback: "Be brief." });
    expect(fb).toMatchObject({ source: "fallback", text: "Be brief.", release: null });
  });

  it("never waits longer than the timeout", async () => {
    const slow = mockFetch(
      (req) =>
        new Promise((_, reject) => {
          req.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    const t0 = Date.now();
    const ins = await client(slow.fetch, { timeoutMs: 50 }).fetchInstructions("front-desk", { fallback: "Hold on." });
    expect(ins.source).toBe("fallback");
    expect(Date.now() - t0).toBeLessThan(1000);
  });

  it("keeps a file cache across processes (Node)", async () => {
    const dir = await mkdtemp(join(tmpdir(), "sopops-"));
    await client(mockFetch(json(AGENT)).fetch, { cache: new FileCache(dir) }).fetchInstructions("front-desk");
    const ins = await client(mockFetch(new TypeError("down")).fetch, { cache: new FileCache(dir) }).fetchInstructions("front-desk");
    expect(ins).toMatchObject({ source: "cache", release: 3 });
  });
});

describe("postSession", () => {
  it("retries server errors and sends the canonical session", async () => {
    const m = mockFetch(json({}, 502), new TypeError("reset"), json({ id: "s1", created: true, updated: false, status: "received", warnings: [] }, 201));
    const out = await client(m.fetch).postSession({ platform: "elevenlabs", external_id: "conv_1", turns: [{ role: "user", text: "hi", offset_s: 1 }] });
    expect(out.id).toBe("s1");
    expect(m.calls).toHaveLength(3);
    expect(await m.calls[2]!.json()).toMatchObject({ platform: "elevenlabs", external_id: "conv_1" });
  });

  it("doesn't retry a refusal", async () => {
    const m = mockFetch(json({ detail: "invalid ingest token" }, 401));
    await expect(client(m.fetch).postSession({ platform: "api", external_id: "x" })).rejects.toThrow(/invalid ingest token/);
    expect(m.calls).toHaveLength(1);
  });
});

describe("elevenLabsSession", () => {
  it("builds startSession options with the prompt override", async () => {
    const m = mockFetch(json(AGENT), json({}));
    const s = await elevenLabsSession(client(m.fetch), { agent: "front-desk" });
    expect(s.sessionOptions.agentId).toBe("agent_123");
    expect(s.sessionOptions.overrides).toEqual({ agent: { prompt: { prompt: "You are the front desk." } } });
    expect(s.sessionOptions.dynamicVariables).toEqual({ sopops_agent: "front-desk", sopops_release: 3 });

    s.sessionOptions.onConnect({ conversationId: "conv_9" });
    await vi.waitFor(() => expect(m.calls).toHaveLength(2));
    expect(m.calls[1]!.url).toBe(`${URL}/runtime/served`);
    expect(await m.calls[1]!.json()).toEqual({ platform: "elevenlabs", call_id: "conv_9", agent: "front-desk", release: 3 });
  });

  it("finds the agent by its ElevenLabs id, and never throws when linking fails", async () => {
    const m = mockFetch(json(AGENT), new TypeError("down"));
    const s = await elevenLabsSession(client(m.fetch), { agentId: "agent_123" });
    expect(m.calls[0]!.url).toBe(`${URL}/agents/${encodeURIComponent("elevenlabs:agent_123")}`);
    await expect(s.link("conv_1")).resolves.toBeUndefined();
  });

  it("refuses an agent that isn't on ElevenLabs", async () => {
    const m = mockFetch(json({ ...AGENT, platform_ref: "livekit:front-desk" }));
    await expect(elevenLabsSession(client(m.fetch), { agent: "front-desk" })).rejects.toThrow(/isn't an ElevenLabs agent/);
  });
});

describe("releasePin", () => {
  it("reads a pin from metadata", () => {
    expect(releasePin('{"sopops_release": 4}')).toBe(4);
    expect(releasePin({ sopops: { release: "5" } })).toBe(5);
    expect(releasePin("nope")).toBeNull();
    expect(releasePin(null)).toBeNull();
  });
});

describe("errors", () => {
  it("are typed", () => {
    expect(new SopopsUnavailable("x")).toBeInstanceOf(SopopsError);
  });
});
