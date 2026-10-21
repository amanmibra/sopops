import { useQuery } from "@tanstack/react-query";
import { Outlet, createFileRoute, useLocation } from "@tanstack/react-router";
import { useEffect, useMemo } from "react";
import { ErrorBanner } from "@/components/bits";
import { Rail } from "@/components/layout";
import { SetupBar } from "@/components/setup";
import { ConvertView } from "@/components/views";
import { q } from "@/lib/api";
import { rememberPage } from "@/lib/last-page";
import { prefs } from "@/lib/prefs";
import { railCols, useRailCollapsed } from "@/lib/rail";
import { cn } from "@/lib/utils";
import { WsContext, makeWs } from "@/lib/workspace";

export const Route = createFileRoute("/w/$ws")({ component: WorkspaceLayout });

/** Loads the workspace (config, unpublished changes, releases) for every page under it. */
function WorkspaceLayout() {
  const { ws } = Route.useParams();
  const { pathname, searchStr } = useLocation();
  const config = useQuery(q.config(ws));
  const draft = useQuery(q.draft(ws));
  const releases = useQuery(q.releases(ws));
  const collapsed = useRailCollapsed();
  useEffect(() => prefs.set("ws", ws), [ws]);
  useEffect(() => rememberPage(ws, pathname, searchStr), [ws, pathname, searchStr]);
  const value = useMemo(
    () => (config.data && draft.data && releases.data ? makeWs(ws, config.data.cfg, draft.data, releases.data, config.data.legacy) : null),
    [ws, config.data, draft.data, releases.data],
  );
  const error = config.error ?? draft.error ?? releases.error;
  if (error && !value)
    return (
      <main className="px-8 py-6">
        <ErrorBanner error={error} />
      </main>
    );
  if (!value) return <main className="text-muted-foreground px-8 py-6">Loading…</main>;
  // A workspace stored in an older sopc format can only be converted (or its releases looked at).
  const convert = value.legacy && !pathname.includes("/releases");
  // Setup is a page of its own; Settings brings its own side menu in place of the mode's rail.
  if (!convert && /^\/w\/[^/]+\/setup$/.test(pathname))
    return (
      <WsContext.Provider value={value}>
        <Outlet />
      </WsContext.Provider>
    );
  if (!convert && /^\/w\/[^/]+\/settings(\/|$)/.test(pathname))
    return (
      <WsContext.Provider value={value}>
        <Outlet />
      </WsContext.Provider>
    );
  return (
    <WsContext.Provider value={value}>
      <div className={cn("grid min-h-[calc(100vh-57px)] grid-cols-1", railCols(collapsed))}>
        <Rail />
        <main className="min-w-0 px-4 pt-5 pb-10 md:px-8 md:pt-6">
          <SetupBar ws={ws} />
          {convert ? <ConvertView /> : <Outlet />}
        </main>
      </div>
    </WsContext.Provider>
  );
}
