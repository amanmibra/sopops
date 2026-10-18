// The workspace's two modes: QA (how sessions went against the instructions) and Instructions (what agents are told).
// A page's mode comes from its route. Settings and the workspace root belong to neither: they keep the mode you came from.
import { wsHref } from "./workspace";

export type Mode = "qa" | "instructions";

export const MODES: { mode: Mode; label: string; tip: string }[] = [
  { mode: "qa", label: "QA", tip: "QA: how sessions went against your instructions" },
  { mode: "instructions", label: "Instructions", tip: "Instructions: what your agents are told" },
];

const SECTIONS: Record<string, Mode> = {
  overview: "qa",
  sessions: "qa",
  insights: "qa",
  patterns: "qa",
  suggestions: "qa",
  report: "qa",
  agents: "instructions",
  instructions: "instructions",
  procedures: "instructions",
  groups: "instructions",
  changes: "instructions",
  releases: "instructions",
  import: "instructions",
  convert: "instructions",
};

/** The first path segment after /w/<ws>/, e.g. "patterns". */
function section(pathname: string): string | undefined {
  return /^\/w\/[^/]+\/([^/?#]+)/.exec(pathname)?.[1];
}

/** The mode a page belongs to, or null for Settings and the workspace root. */
export function routeMode(pathname: string): Mode | null {
  const s = section(pathname);
  return (s && SECTIONS[s]) || null;
}

// The last page of each mode and the last mode, per workspace, for this tab. Storage can be missing or refuse.
const key = (ws: string, what: string) => `sopops.nav.${ws}.${what}`;
function read(k: string): string | null {
  try {
    return sessionStorage.getItem(k);
  } catch {
    return null;
  }
}
function write(k: string, v: string) {
  try {
    sessionStorage.setItem(k, v);
  } catch {
    /* private mode */
  }
}

const isMode = (v: unknown): v is Mode => v === "qa" || v === "instructions";

/** The mode to show: the page's own, else the `from` search param, else the last one used here, else QA. */
export function currentMode(ws: string, pathname: string, from?: unknown): Mode {
  const own = routeMode(pathname);
  if (own) return own;
  if (isMode(from)) return from;
  const last = read(key(ws, "mode"));
  return isMode(last) ? last : "qa";
}

/** Remember where you are, so switching back to this mode returns here. New-item forms aren't remembered. */
export function rememberPage(ws: string, pathname: string, searchStr: string) {
  const mode = routeMode(pathname);
  if (!mode) return;
  write(key(ws, "mode"), mode);
  if (!pathname.endsWith("/new")) write(key(ws, mode), pathname + searchStr);
}

/** Where switching to a mode goes: its last page in this workspace, or its first page. */
export function modeHref(ws: string, mode: Mode): string {
  const last = read(key(ws, mode));
  if (last && decodeURIComponent(last).startsWith(`/w/${ws}/`) && routeMode(last.split("?")[0]) === mode) return last;
  return wsHref(ws, mode === "qa" ? "overview" : "agents");
}
