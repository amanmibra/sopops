/**
 * sopops client: fetch an agent's instructions at call start, and send each finished session back.
 * Runs on Node 18+, browsers and edge runtimes (it uses only `fetch`). Platform adapters are subpaths:
 * `sopops/elevenlabs`, `sopops/livekit`. A file cache for Node is in `sopops/node`.
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
  /** Placeholders still in `text`: ones you passed no value for. Empty unless you passed `variables`. */
  unfilled?: string[];
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

export class SopOpsError extends Error {
  readonly status: number | null;
  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "SopOpsError";
    this.status = status;
  }
}

/** No instructions: sopops failed, nothing was cached, and no fallback was given. */
export class SopOpsUnavailable extends SopOpsError {
  constructor(message: string) {
    super(message);
    this.name = "SopOpsUnavailable";
  }
}

export interface SopOpsOptions {
  /** The workspace's API URL, e.g. `https://sopops.internal/v1/workspaces/prod`. Default: `SOPOPS_URL` (Node). */
  url?: string;
  /** A workspace token (Settings → Tokens): scope `runtime` to fetch prompts, `ingest` to send sessions. Default: `SOPOPS_TOKEN` (Node). Don't ship it to browsers you don't trust. */
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
  /** Values for `{{placeholders}}` this call knows and sopops can't: the time, the caller, anything you work out per
   * call. Filled here, in your process, so these never reach sopops. What's left over is in `unfilled`. */
  variables?: Record<string, string | number | boolean>;
}

/** A canonical sopops session (see docs/api/sessions.md, "The canonical format"). */
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
  /** An A/B test this session is part of. Without one, sopops still compares an agent's versions by their config. */
  test?: string | null;
  /** What the agent ran with: `llm`, `stt`, `tts` (`<provider>/<model>`), `voice`, `version`, any other setting.
   * A value can be a whole JSON object, so a variant's entire config can go in as it is. */
  config?: AgentConfig;
}

/** A setting's value: a scalar, or a whole JSON object or array (up to 8,192 characters of JSON, 8 levels deep). */
export type ConfigValue = string | number | boolean | null | ConfigValue[] | { [k: string]: ConfigValue };

/** Settings a session ran with. Sessions with the same agent, release and config are one version in sopops. */
export type AgentConfig = Record<string, ConfigValue>;

type Turn = NonNullable<SessionIn["turns"]>[number];
type ToolCall = NonNullable<SessionIn["tool_calls"]>[number];

/** What `openSession` takes: a session's identity and context, before its turns exist. */
export type SessionOpen = Pick<SessionIn, "platform" | "external_id" | "channel" | "agent" | "release" | "version_ref" | "started_at" | "metadata" | "test" | "config">;

/** One streamed event. `seq` counts from 0 per session; order doesn't matter and a resent `seq` is ignored. */
export type SessionEvent = { seq: number; type: "turn"; turn: Turn } | { seq: number; type: "tool_call"; tool_call: ToolCall };

/** Runtime guidance from sopops. Not yet sent: reserved for a later version. */
export type Guidance = Record<string, unknown>;

export interface StreamOptions {
  /** Send at most this often. Default 500. */
  flushMs?: number;
  /** Events per request. Default 50. */
  maxBatch?: number;
  /** Events waiting to be sent; beyond this they're dropped. Default 1000. */
  maxQueue?: number;
  /** Not yet used: reserved for runtime guidance in a later version. */
  onGuidance?: (guidance: Guidance) => void;
}

export interface SessionIngested {
  id: string;
  created: boolean;
  updated: boolean;
  status: string;
  warnings: string[];
}

/** A release pinned for this call in metadata: `{"sopops_release": 3}` or `{"sopops": {"release": 3}}`, as JSON or an object. */
const PLACEHOLDER = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;

/** `{{placeholders}}` replaced with your values. One with no value is left as it is, the way sopops leaves a
 * placeholder it has no value for, so a missing value never mangles the instructions. */
export function fill(text: string, variables: Record<string, string | number | boolean>): string {
  return text.replace(PLACEHOLDER, (whole, name: string) => (name in variables ? String(variables[name]) : whole));
}

/** The placeholders still in the text, in order, without repeats. */
export function unfilled(text: string): string[] {
  return [...new Set(Array.from(text.matchAll(PLACEHOLDER), (m) => m[1] as string))];
}

const names = (xs: string[]) => xs.map((n) => `{{${n}}}`).join(", ");

/** Fill what this call knows, and say what didn't line up. A mismatch is never fatal: the call goes on with the
 * placeholder as it is, which is what sopops serves anyway. */
function filled(got: Instructions, variables: FetchOptions["variables"]): Instructions {
  if (!variables || Object.keys(variables).length === 0) return got;
  const wanted = new Set(unfilled(got.text));
  const text = fill(got.text, variables);
  const left = unfilled(text);
  if (left.length) console.warn(`sopops: no value for ${names(left)} in ${got.agent}'s instructions; it stays as a placeholder`);
  const spare = Object.keys(variables).filter((k) => !wanted.has(k));
  if (spare.length) console.warn(`sopops: ${names(spare)} has no placeholder in ${got.agent}'s instructions; check the spelling`);
  return { ...got, text, unfilled: left };
}

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

export class SopOps {
  readonly url: string;
  readonly token: string | undefined;
  readonly timeoutMs: number;
  private cache: InstructionCache;
  private fetchImpl: typeof fetch;

  constructor(options: SopOpsOptions = {}) {
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
   * copy (`source: "cache"`), else `fallback`, else throws SopOpsUnavailable.
   */
  async fetchInstructions(agent: string, options: FetchOptions = {}): Promise<Instructions> {
    const release = options.release ?? null;
    const key = `${this.url}\n${agent}\n${release ?? "live"}`;
    try {
      const qs = release != null ? `?release=${encodeURIComponent(String(release))}` : "";
      const res = await this.request(`/agents/${encodeURIComponent(agent)}${qs}`, { headers: this.headers() }, this.timeoutMs);
      if (!res.ok) throw new SopOpsError(`sopops answered ${res.status} for agent '${agent}': ${await detail(res)}`, res.status);
      const b = (await res.json()) as Record<string, unknown>;
      if (typeof b.prompt !== "string") throw new SopOpsError("sopops returned no prompt");
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
        await this.cache.set(key, got);   // unfilled, so the next call fills it with its own values
      } catch (e) {
        console.warn(`sopops: couldn't cache instructions for ${agent}: ${String(e)}`);
      }
      return filled(got, options.variables);
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
        return filled({ ...cached, source: "cache" }, options.variables);
      }
      if (options.fallback !== undefined) {
        console.error(`sopops: using the fallback instructions for ${agent}, nothing cached: ${reason}`);
        return filled({ text: options.fallback, release: null, agent, blocks: [], hash: null, platformRef: null, tools: [], toolSops: [], source: "fallback" }, options.variables);
      }
      throw new SopOpsUnavailable(`no instructions for '${agent}': ${reason}`);
    }
  }

  /** Send one finished session. Safe to repeat (an upsert on platform + external_id). Retries network errors, 429 and 5xx. */
  async postSession(session: SessionIn, options: { retries?: number; timeoutMs?: number } = {}): Promise<SessionIngested> {
    const retries = options.retries ?? 2;
    let last: Error = new SopOpsError("not sent");
    for (let attempt = 0; attempt <= retries; attempt++) {
      if (attempt) await sleep(500 * 2 ** (attempt - 1));
      let res: Response;
      try {
        res = await this.request("/sessions", { method: "POST", headers: this.headers(true), body: JSON.stringify(session) }, options.timeoutMs ?? 5000);
      } catch (e) {
        last = new SopOpsError(`couldn't reach sopops: ${e instanceof Error ? e.name : String(e)}`);
        continue;
      }
      if (res.status === 429 || res.status >= 500) {
        last = new SopOpsError(`sopops answered ${res.status}: ${await detail(res)}`, res.status);
        continue;
      }
      if (!res.ok) throw new SopOpsError(`sopops refused the session (${res.status}): ${await detail(res)}`, res.status);
      return (await res.json()) as SessionIngested;
    }
    throw last;
  }

  /**
   * Open a session while the call runs, so its events can be sent as they happen. Safe to repeat (keyed by
   * platform + external_id, like `postSession`). Most callers want `stream()` instead.
   */
  async openSession(session: SessionOpen, options: { timeoutMs?: number } = {}): Promise<{ id: string; created: boolean }> {
    return (await this.post("/sessions/open", session, options.timeoutMs ?? 3000)) as { id: string; created: boolean };
  }

  /** Add events to an open session. The full session sent with `postSession` at the end replaces what was streamed. */
  async appendEvents(platform: string, externalId: string, events: SessionEvent[], options: { timeoutMs?: number } = {}): Promise<{ accepted: number; duplicates: number }> {
    const path = `/sessions/${encodeURIComponent(platform)}/${encodeURIComponent(externalId)}/events`;
    return (await this.post(path, { events }, options.timeoutMs ?? 3000)) as { accepted: number; duplicates: number };
  }

  /** Stream a session's turns and tool calls while the call runs (see `SessionStream`). */
  stream(session: SessionOpen, options: StreamOptions = {}): SessionStream {
    return new SessionStream(this, session, options);
  }

  /** One POST, no retries. */
  private async post(path: string, body: unknown, timeoutMs: number): Promise<unknown> {
    let res: Response;
    try {
      res = await this.request(path, { method: "POST", headers: this.headers(true), body: JSON.stringify(body) }, timeoutMs);
    } catch (e) {
      throw new SopOpsError(`couldn't reach sopops: ${e instanceof Error ? e.name : String(e)}`);
    }
    if (!res.ok) throw new SopOpsError(`sopops answered ${res.status}: ${await detail(res)}`, res.status);
    return res.json();
  }

  /**
   * Tell sopops which release a call ran on once the platform's id for it is known (an ElevenLabs conversation
   * id, a Vapi call id), so the session its post-call webhook sends links to that release.
   */
  async recordServed(served: { platform: "elevenlabs" | "vapi"; callId: string; agent: string; release: number }, options: { timeoutMs?: number } = {}): Promise<void> {
    const body = { platform: served.platform, call_id: served.callId, agent: served.agent, release: served.release };
    const res = await this.request("/runtime/served", { method: "POST", headers: this.headers(true), body: JSON.stringify(body) }, options.timeoutMs ?? 3000);
    if (!res.ok) throw new SopOpsError(`sopops answered ${res.status}: ${await detail(res)}`, res.status);
  }
}

/**
 * Sends one session's turns and tool calls to sopops while the call runs, in batches. `turn` and `toolCall` never
 * block and never throw: when the queue is full, or sopops fails, events are logged and dropped. Send the full
 * session with `postSession` at the end as usual; it replaces what was streamed.
 *
 *   const stream = sopops.stream({ platform: "api", external_id: callId, agent: "tonys-pizza", release: 3 });
 *   stream.turn({ role: "user", text: "Delivery please.", at: new Date().toISOString() });
 *   await stream.close();
 *   await sopops.postSession(fullSession);
 */
/** The agent's instructions in one call, for code that doesn't keep a client around.
 *
 * `agent` is the sopc agent id or its platform ref (`livekit:tonys-pizza`). `release` pins one; leave it out for
 * the live release. `url` and `token` default to `SOPOPS_URL` and `SOPOPS_TOKEN`, as `SopOps` reads them.
 * Falls back the same way the method does: the cache, then `fallback`, else `SopOpsUnavailable`.
 *
 *     const ins = await fetchInstructions("tonys-pizza");       // the live release
 *     const ins = await fetchInstructions("tonys-pizza", 14);   // pinned, e.g. one arm of an A/B test
 *
 * It builds a client per call, so in anything hot keep a `SopOps` and call its `fetchInstructions`. */
export async function fetchInstructions(
  agent: string,
  release?: number | null,
  options: SopOpsOptions & { fallback?: string } = {},
): Promise<Instructions> {
  const { fallback, ...client } = options;
  return new SopOps(client).fetchInstructions(agent, { release: release ?? null, fallback });
}

export class SessionStream {
  readonly session: SessionOpen;
  /** Not yet used: reserved for runtime guidance in a later version. */
  readonly onGuidance: ((guidance: Guidance) => void) | undefined;
  sent = 0;
  dropped = 0;
  private seq = 0;
  private queue: SessionEvent[] = [];
  private opened = false;
  private closed = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private sending: Promise<void> = Promise.resolve();
  private readonly flushMs: number;
  private readonly maxBatch: number;
  private readonly maxQueue: number;

  constructor(
    private readonly sopops: SopOps,
    session: SessionOpen,
    options: StreamOptions = {},
  ) {
    this.session = Object.fromEntries(Object.entries(session).filter(([, v]) => v != null)) as SessionOpen;
    this.onGuidance = options.onGuidance;
    this.flushMs = options.flushMs ?? 500;
    this.maxBatch = options.maxBatch ?? 50;
    this.maxQueue = options.maxQueue ?? 1000;
  }

  /** Queue a turn (`{role, text, at | offset_s, ...}`, as in the canonical format). */
  turn(turn: Turn): void {
    this.add({ seq: this.seq, type: "turn", turn });
  }

  /** Queue a tool call (`{name, arguments, result, is_error, call_id, at | offset_s}`). */
  toolCall(call: ToolCall): void {
    this.add({ seq: this.seq, type: "tool_call", tool_call: call });
  }

  /** Send what's queued and stop, giving up after `timeoutMs`. Never throws. */
  async close(timeoutMs = 1000): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    clearTimeout(this.timer);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const gaveUp = new Promise<boolean>((r) => (timer = setTimeout(() => r(true), timeoutMs)));
    const done = this.flush().then(() => false);
    if (await Promise.race([done, gaveUp])) console.warn(`sopops: stopped streaming session ${this.session.external_id} with events unsent`);
    clearTimeout(timer);
  }

  private add(event: SessionEvent): void {
    if (this.closed) return;
    this.seq++;
    if (this.queue.length >= this.maxQueue) {
      if (++this.dropped === 1) console.warn(`sopops: the event queue for session ${this.session.external_id} is full; dropping events`);
      return;
    }
    this.queue.push(event);
    if (this.queue.length >= this.maxBatch) void this.flush();
    else this.timer ??= setTimeout(() => void this.flush(), this.flushMs);
  }

  /** Send everything queued, one request at a time. */
  private flush(): Promise<void> {
    clearTimeout(this.timer);
    this.timer = undefined;
    this.sending = this.sending.then(async () => {
      while (this.queue.length) await this.send(this.queue.splice(0, this.maxBatch));
    });
    return this.sending;
  }

  private async send(batch: SessionEvent[]): Promise<void> {
    try {
      if (!this.opened) {
        await this.sopops.openSession(this.session);
        this.opened = true;
      }
      await this.sopops.appendEvents(this.session.platform, this.session.external_id, batch);
      this.sent += batch.length;
    } catch (e) {
      this.dropped += batch.length;
      console.warn(`sopops: dropped ${batch.length} events for session ${this.session.external_id}: ${e instanceof Error ? e.message : String(e)}`);
    }
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
