// Support sessions from the browser's side: the state the band reads, the teams one can be started against, the
// record a team keeps of who looked, and the countdown the band ticks. A session dies at `until`, so the state is
// polled rather than fetched once: the band has to go by itself when the session ends, with nobody clicking anything.
import { queryOptions } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { call, sdk } from "./api";

/** How often the banner asks whether its session is still alive. */
export const POLL_MS = 15_000;

export const supportQ = {
  /** The band's state, and whether you're staff at all. Off (404) or signed-in-less (409), it stops asking. */
  state: (enabled = true) =>
    queryOptions({
      queryKey: ["support", "state"],
      queryFn: () => call(sdk.getSupportState()),
      enabled,
      retry: false,
      staleTime: 0,
      refetchInterval: (query) => (query.state.error ? false : POLL_MS),
    }),
  /** What your own team sees: whether support access is allowed, and every session against you. */
  org: () => queryOptions({ queryKey: ["org", "support"], queryFn: () => call(sdk.getOrgSupport()), retry: false }),
  /** The teams a session can be started against, for the start page's select. Staff only; anyone else gets a 404. */
  teams: (enabled = true) =>
    queryOptions({ queryKey: ["support", "teams"], queryFn: () => call(sdk.listSupportTeams()), enabled, retry: false }),
};

const secondsLeft = (end: number) => (end ? Math.max(0, Math.round((end - Date.now()) / 1000)) : 0);

/** Seconds until `until`, counted down once a second here rather than asked of the server once a second. */
export function useCountdown(until: string | null | undefined): number {
  const end = until ? Date.parse(until) : 0;
  const [left, setLeft] = useState(() => secondsLeft(end));
  useEffect(() => {
    setLeft(secondsLeft(end));
    if (!end) return;
    const timer = window.setInterval(() => setLeft(secondsLeft(end)), 1000);
    return () => window.clearInterval(timer);
  }, [end]);
  return left;
}

/** 9:05, or 1:04:12 for a session over an hour. */
export function clock(seconds: number): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** How long a session was allowed to run, as "15 minutes" or "4 hours". */
export function span(startedAt: string, until: string): string {
  const minutes = Math.max(1, Math.round((Date.parse(until) - Date.parse(startedAt)) / 60_000));
  if (minutes % 60 === 0) {
    const hours = minutes / 60;
    return `${hours} ${hours === 1 ? "hour" : "hours"}`;
  }
  return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
}

export const START_HREF = "/support";
export const MAX_MINUTES = 480;
export const DEFAULT_MINUTES = 60;
/** The lengths the start page offers, in the order it offers them. */
export const LENGTHS: { minutes: number; label: string }[] = [
  { minutes: 15, label: "15 minutes" },
  { minutes: 60, label: "1 hour" },
  { minutes: 240, label: "4 hours" },
  { minutes: 480, label: "8 hours" },
];
