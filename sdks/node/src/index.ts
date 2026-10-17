/**
 * sopops client: fetch an agent's instructions at call start, and send each finished session back.
 * Runs on Node 18+, browsers and edge runtimes (it uses only `fetch`). Platform adapters are subpaths:
 * `sopops-client/elevenlabs`. A file cache for Node is in `sopops-client/node`.
 */

export type Source = "live" | "cache" | "fallback";

/** One file the prompt was built from, in prompt order. */
export interface Block {
  kind: string;
  id: string;
  path: string | null;
  version: number | null;
}

export interface Instructions {
  /** The compiled prompt. */
  text: string;
  /** The sopops release it came from; null for a fallback. */
  release: number | null;
  /** The sopc agent id. */
  agent: string;
  blocks: Block[];
  hash: string | null;
  /** `livekit:tonys-pizza`, `elevenlabs:<agent id>`, `vapi:<assistant id>`. */
  platformRef: string | null;
  tools: string[];
  toolSops: string[];
  /** `live` from sopops, `cache` the last good copy (sopops failed), `fallback` the text you passed. */
  source: Source;
}

/** Where the last good instructions are kept. The default keeps them in memory for the process's lifetime. */
export interface InstructionCache {
  get(key: string): Promise<Instructions | undefined> | Instructions | undefined;
  set(key: string, value: Instructions): Promise<void> | void;
}

export class MemoryCache implements InstructionCache {
  private items = new Map<string, Instructions>();
  get(key: string) {
    return this.items.get(key);
  }
  set(key: string, value: Instructions) {
    this.items.set(key, value);
  }
}

const sharedMemory = new MemoryCache();

export class SopopsError extends Error {
  readonly status: number | null;
  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "SopopsError";
    this.status = status;
  }
}

/** No instructions: sopops failed, nothing was cached, and no fallback was given. */
export class SopopsUnavailable extends SopopsError {
  constructor(message: string) {
    super(message);
    this.name = "SopopsUnavailable";
  }
}

export interface SopopsOptions {
  /** The workspace's API URL, e.g. `https://sopops.internal/v1/workspaces/prod`. Default: `SOPOPS_URL` (Node). */
  url?: string;
  /** The workspace's runtime token (Settings → Ingest token). Default: `SOPOPS_TOKEN` (Node). Don't ship it to browsers you don't trust. */
  token?: string;
  /** How long the call-start fetch may take before the cache is used. Default 2000. */
  timeoutMs?: number;
  cache?: InstructionCache;
  /** A fetch implementation; default `globalThis.fetch`. */
  fetch?: typeof fetch;
}

export interface FetchOptions {
  /** Pin a release (A/B tests); default: the live one. */
  release?: number | null;
  /** Text to use if sopops is down and nothing is cached. */
  fallback?: string;
}

/** A canonical sopops session (see API.md, "The canonical format"). */
export interface SessionIn {
  platform: "livekit" | "elevenlabs" | "vapi" | "otel" | "import" | "api";
  external_id: string;
  channel?: "voice" | "text";
  agent?: string | null;
  release?: number | null;
  version_ref?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  partial?: boolean;
  turns?: Array<{ role: string; text?: string; offset_s?: number; at?: string | number; [k: string]: unknown }>;
  tool_calls?: Array<{ name: string; arguments?: unknown; result?: unknown; is_error?: boolean; call_id?: string; offset_s?: number; at?: string | number; [k: string]: unknown }>;
  metadata?: Record<string, unknown>;
}

export interface SessionIngested {
  id: string;
  created: boolean;
  updated: boolean;
  status: string;
  warnings: string[];
}

/** A release pinned for this call in metadata: `{"sopops_release": 3}` or `{"sopops": {"release": 3}}`, as JSON or an object. */
export function releasePin(metadata: string | Record<string, unknown> | null | undefined): number | null {
  if (!metadata) return null;
  let m: unknown = metadata;
  if (typeof m === "string") {
    try {
      m = JSON.parse(m);
    } catch {
      return null;
    }
  }
  if (!m || typeof m !== "object") return null;
  const o = m as Record<string, unknown>;
  const nested = o.sopops && typeof o.sopops === "object" ? (o.sopops as Record<string, unknown>).release : undefined;
  const v = o.sopops_release ?? nested;
  if (typeof v === "boolean" || v == null) return null;
  const n = Number(v);
  return Number.isInteger(n) ? n : null;
}

function env(name: string): string | undefined {
  const p = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process;
  return p?.env?.[name];
}

export class Sopops {
  readonly url: string;
  readonly token: string | undefined;
  readonly timeoutMs: number;
  private cache: InstructionCache;
  private fetchImpl: typeof fetch;

  constructor(options: SopopsOptions = {}) {
    const url = options.url ?? env("SOPOPS_URL");
    if (!url) throw new Error("pass url or set SOPOPS_URL to the workspace's API URL, e.g. http://sopops:8484/v1/workspaces/prod");
    this.url = url.replace(/\/+$/, "");
    this.token = options.token ?? env("SOPOPS_TOKEN") ?? env("SOPOPS_INGEST_TOKEN");
    this.timeoutMs = options.timeoutMs ?? 2000;
    this.cache = options.cache ?? sharedMemory;
    const f = options.fetch ?? globalThis.fetch;
    if (!f) throw new Error("no fetch available; pass options.fetch");
    this.fetchImpl = f.bind(globalThis);
  }

  private headers(json = false): Record<string, string> {
    const h: Record<string, string> = {};
    if (this.token) h.Authorization = `Bearer ${this.token}`;
    if (json) h["Content-Type"] = "application/json";
    return h;
  }

  private async request(path: string, init: RequestInit, timeoutMs: number): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await this.fetchImpl(this.url + path, { ...init, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * The agent's instructions in the live release, or a pinned one. `agent` is the sopc agent id or its platform
   * ref (`elevenlabs:<agent id>`). Never waits longer than `timeoutMs`: on any failure it returns the last good
   * copy (`source: "cache"`), else `fallback`, else throws SopopsUnavailable.
   */
  async fetchInstructions(agent: string, options: FetchOptions = {}): Promise<Instructions> {
    const release = options.release ?? null;
    const key = `${this.url}\n${agent}\n${release ?? "live"}`;
    try {
      const qs = release != null ? `?release=${encodeURIComponent(String(release))}` : "";
      const res = await this.request(`/agents/${encodeURIComponent(agent)}${qs}`, { headers: this.headers() }, this.timeoutMs);
      if (!res.ok) throw new SopopsError(`sopops answered ${res.status} for agent '${agent}': ${await detail(res)}`, res.status);
      const b = (await res.json()) as Record<string, unknown>;
      if (typeof b.prompt !== "string") throw new SopopsError("sopops returned no prompt");
      const got: Instructions = {
        text: b.prompt,
        release: (b.release as number) ?? null,
        agent: (b.agent as string) ?? agent,
        blocks: (b.components as Block[]) ?? [],
        hash: (b.hash as string) ?? null,
        platformRef: (b.platform_ref as string) ?? null,
        tools: (b.tools as string[]) ?? [],
        toolSops: (b.tool_sops as string[]) ?? [],
        source: "live",
      };
      try {
        await this.cache.set(key, got);
      } catch (e) {
        console.warn(`sopops: couldn't cache instructions for ${agent}: ${String(e)}`);
      }
      return got;
    } catch (e) {
      const reason = e instanceof Error ? (e.name === "AbortError" ? `timed out after ${this.timeoutMs}ms` : e.message) : String(e);
      let cached: Instructions | undefined;
      try {
        cached = await this.cache.get(key);
      } catch {
        cached = undefined;
      }
      if (cached) {
        console.warn(`sopops: using the cached instructions for ${agent} (release ${cached.release}): ${reason}`);
        return { ...cached, source: "cache" };
      }
      if (options.fallback !== undefined) {
        console.error(`sopops: using the fallback instructions for ${agent}, nothing cached: ${reason}`);
        return { text: options.fallback, release: null, agent, blocks: [], hash: null, platformRef: null, tools: [], toolSops: [], source: "fallback" };
      }
      throw new SopopsUnavailable(`no instructions for '${agent}': ${reason}`);
    }
  }

  /** Send one finished session. Safe to repeat (an upsert on platform + external_id). Retries network errors, 429 and 5xx. */
  async postSession(session: SessionIn, options: { retries?: number; timeoutMs?: number } = {}): Promise<SessionIngested> {
    const retries = options.retries ?? 2;
    let last: Error = new SopopsError("not sent");
    for (let attempt = 0; attempt <= retries; attempt++) {
      if (attempt) await sleep(500 * 2 ** (attempt - 1));
      let res: Response;
      try {
        res = await this.request("/sessions", { method: "POST", headers: this.headers(true), body: JSON.stringify(session) }, options.timeoutMs ?? 5000);
      } catch (e) {
        last = new SopopsError(`couldn't reach sopops: ${e instanceof Error ? e.name : String(e)}`);
        continue;
      }
      if (res.status === 429 || res.status >= 500) {
        last = new SopopsError(`sopops answered ${res.status}: ${await detail(res)}`, res.status);
        continue;
      }
      if (!res.ok) throw new SopopsError(`sopops refused the session (${res.status}): ${await detail(res)}`, res.status);
      return (await res.json()) as SessionIngested;
    }
    throw last;
  }

  /**
   * Tell sopops which release a call ran on once the platform's id for it is known (an ElevenLabs conversation
   * id, a Vapi call id), so the session its post-call webhook sends links to that release.
   */
  async recordServed(served: { platform: "elevenlabs" | "vapi"; callId: string; agent: string; release: number }, options: { timeoutMs?: number } = {}): Promise<void> {
    const body = { platform: served.platform, call_id: served.callId, agent: served.agent, release: served.release };
    const res = await this.request("/runtime/served", { method: "POST", headers: this.headers(true), body: JSON.stringify(body) }, options.timeoutMs ?? 3000);
    if (!res.ok) throw new SopopsError(`sopops answered ${res.status}: ${await detail(res)}`, res.status);
  }
}

async function detail(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  try {
    const d = (JSON.parse(text) as { detail?: unknown }).detail;
    if (d) return typeof d === "string" ? d : JSON.stringify(d);
  } catch {
    /* not JSON */
  }
  return text.slice(0, 200);
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
