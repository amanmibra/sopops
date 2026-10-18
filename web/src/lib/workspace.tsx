// The open workspace's data, loaded once by the /w/$ws layout and read by every page under it.
import { createContext, useContext } from "react";
import type { ConfigResponse, DraftItem, DraftResponse, Legacy, ReleaseSummary } from "./api";
import type { Kind } from "./kinds";
import { Model } from "./model";

export type Ws = {
  ws: string;
  cfg: ConfigResponse;
  model: Model;
  draft: DraftResponse;
  releases: ReleaseSummary[];
  legacy: Legacy;
  /** The item's unpublished change, if it has one. */
  changeOf: (kind: Kind, id: string) => DraftItem | undefined;
  /** Where an item's page is. */
  href: (kind: Kind, id?: string, tab?: string) => string;
};

export const WsContext = createContext<Ws | null>(null);

export function useWs(): Ws {
  const ws = useContext(WsContext);
  if (!ws) throw new Error("useWs outside a workspace");
  return ws;
}

export function makeWs(ws: string, cfg: ConfigResponse, draft: DraftResponse, releases: ReleaseSummary[], legacy: Legacy): Ws {
  return {
    ws,
    cfg,
    model: new Model(cfg),
    draft,
    releases,
    legacy,
    changeOf: (kind, id) => draft.items.find((i) => i.kind === kind && i.id === id),
    href: (kind, id, tab) => itemHref(ws, kind, id, tab),
  };
}

const ROUTES: Record<Kind, string> = { agent: "agents", instruction: "instructions", procedure: "procedures", group: "groups", settings: "settings" };

export function itemHref(ws: string, kind: Kind, id?: string, tab?: string): string {
  const base = `/w/${encodeURIComponent(ws)}/${ROUTES[kind]}`;
  if (kind === "settings") return `${base}/${tab ?? "defaults"}`;
  return [base, id ? encodeURIComponent(id) : "", id && tab ? tab : ""].filter(Boolean).join("/");
}

export const wsHref = (ws: string, ...parts: (string | number)[]) =>
  [`/w/${encodeURIComponent(ws)}`, ...parts.map((p) => encodeURIComponent(String(p)))].join("/");

/** Settings' sections, each on its own page. A bare /settings link lands on the first. */
export type SettingsSection = "platforms" | "github" | "access" | "defaults" | "history" | "checks";
export const settingsHref = (ws: string, section: SettingsSection = "platforms") => wsHref(ws, "settings", section);
