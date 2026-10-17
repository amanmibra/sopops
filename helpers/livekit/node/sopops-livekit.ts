// Send each LiveKit session to sopops when it ends. Copy this file next to your agent.
//
//   const session = new voice.AgentSession({ ..., userData: { sopopsRelease: sop.release } });
//   recordSession(ctx, session);
//
// Env: SOPOPS_URL (http://sopops:8484/v1/workspaces/<ws>) and SOPOPS_TOKEN (a token with the ingest scope, Settings → Tokens in sopops).
import type { JobContext, voice } from "@livekit/agents";

type Item = Record<string, any>;

export function recordSession(ctx: JobContext, session: voice.AgentSession<any>, agent?: string): void {
  const startedAt = new Date().toISOString();
  ctx.addShutdownCallback(async () => {
    const turns: Item[] = [];
    const tools = new Map<string, Item>();
    for (const item of (session.history.toJSON({ excludeTimestamp: false }) as { items: Item[] }).items) {
      const at = item.createdAt ?? item.created_at; // milliseconds in Node; sopops reads either
      const callId = item.callId ?? item.call_id;
      if (item.type === "message") {
        const text = (item.content ?? []).filter((c: unknown) => typeof c === "string").join(" ");
        turns.push({ role: item.role, text, at, interrupted: item.interrupted ?? false });
      } else if (item.type === "function_call") {
        const args = item.args ?? item.arguments;
        turns.push({ role: "tool", text: `${item.name}(${args})`, name: item.name, at });
        tools.set(callId, { name: item.name, arguments: args, call_id: callId, at });
      } else if (item.type === "function_call_output" && tools.has(callId)) {
        Object.assign(tools.get(callId)!, { result: item.output, is_error: item.isError ?? item.is_error ?? false });
      }
    }
    let release: number | undefined;
    try {
      release = (session.userData as { sopopsRelease?: number })?.sopopsRelease;
    } catch {} // userData throws when unset
    const body = {
      platform: "livekit", external_id: ctx.job.id, agent: agent ?? (ctx.job.agentName || undefined), release,
      started_at: startedAt, ended_at: new Date().toISOString(),
      turns, tool_calls: [...tools.values()], metadata: { room: ctx.room.name },
    };
    const res = await fetch(`${process.env.SOPOPS_URL}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${process.env.SOPOPS_TOKEN ?? process.env.SOPOPS_INGEST_TOKEN}` },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000), // stay well inside the job's shutdown budget
    });
    if (!res.ok) throw new Error(`sopops: ${res.status} ${await res.text()}`);
  });
}
