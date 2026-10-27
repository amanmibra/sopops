// Live session events over SSE: refetch what changed, and remember which sessions just arrived.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { SessionEvent } from "./api";
import { DEMO } from "./demo";
import { apiUrl } from "./paths";

export function useSessionStream(ws: string) {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [fresh, setFresh] = useState<ReadonlySet<string>>(new Set());
  const timers = useRef(new Map<string, number>());

  useEffect(() => {
    if (DEMO) return;
    const source = new EventSource(apiUrl(`/v1/workspaces/${encodeURIComponent(ws)}/sessions/stream`));
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false); // EventSource reconnects by itself
    source.addEventListener("session", (msg) => {
      const e = JSON.parse((msg as MessageEvent).data) as SessionEvent;
      void qc.invalidateQueries({ queryKey: ["ws", ws, "sessions"] });
      void qc.invalidateQueries({ queryKey: ["ws", ws, "session", e.id] });
      if (e.type !== "created") return;
      setFresh((s) => new Set(s).add(e.id));
      window.clearTimeout(timers.current.get(e.id));
      timers.current.set(
        e.id,
        window.setTimeout(() => setFresh((s) => new Set([...s].filter((x) => x !== e.id))), 6000),
      );
    });
    const pending = timers.current;
    return () => {
      source.close();
      pending.forEach((t) => window.clearTimeout(t));
      pending.clear();
      setConnected(false);
    };
  }, [ws, qc]);

  return { connected, fresh };
}

/** Repository events (synced from GitHub, a sync conflict, a pull request merged, the connection broken) on the same
 * stream: refetch the workspace's queries, so the repo line, drafts and imports update without polling. */
export function useRepoStream(ws: string) {
  const qc = useQueryClient();
  useEffect(() => {
    if (DEMO) return;
    const source = new EventSource(apiUrl(`/v1/workspaces/${encodeURIComponent(ws)}/sessions/stream`));
    source.addEventListener("repo", () => void qc.invalidateQueries({ queryKey: ["ws", ws] }));
    return () => source.close();
  }, [ws, qc]);
}
