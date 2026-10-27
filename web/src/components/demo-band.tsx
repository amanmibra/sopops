// The demo's bands. The demo build's: what this is, the tour, and the way to try sopops for real (the tour starts by
// itself the first time). The app's, over the server's demo workspace: the tour, and the way back to your own.
import { useLocation } from "@tanstack/react-router";
import { useEffect } from "react";
import type { AuthState } from "@/lib/api";
import { DEMO_WS, SIGN_UP_URL, TOUR_ASKED } from "@/lib/demo";
import { wsHref } from "@/lib/workspace";
import { HrefLink } from "./href-link";
import { useBand } from "./support";
import { startTour, tourRunning } from "./tour";

const SEEN = "sopops.demo.toured";

export function DemoBand() {
  const band = useBand();

  useEffect(() => {
    try {
      if (!TOUR_ASKED && (localStorage.getItem(SEEN) || tourRunning())) return;
      localStorage.setItem(SEEN, "1");
    } catch {
      // no storage: start it every time rather than never
    }
    startTour(DEMO_WS);
  }, []);

  const link = "t-label border-background hover:bg-background hover:text-foreground cursor-pointer border px-3 py-1 hover:no-underline";
  return (
    <div ref={band} role="region" aria-label="Demo" className="bg-foreground text-background sticky top-0 z-30">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2 md:px-6">
        <span className="min-w-0 flex-1 text-sm">
          <span className="sm:hidden">sopops with sample data.</span>
          <span className="max-sm:hidden">You're looking at sopops with sample data: a restaurant group's four voice agents and 300 of their calls. Explore freely.</span>
        </span>
        <button type="button" onClick={() => startTour(DEMO_WS)} className={link}>
          Take the tour
        </button>
        <a href={SIGN_UP_URL} target="_blank" rel="noopener" className={`${link} text-background`}>
          Sign up
        </a>
      </div>
    </div>
  );
}

/** In the app, over the server's demo workspace for someone outside its team: what this is, the tour, and the way back
 *  to their own workspace. Nothing here can be changed; the server refuses it, and says so. */
export function DemoWorkspaceBand({ auth }: { auth: AuthState }) {
  const demo = auth.demo_workspace;
  const { pathname } = useLocation();
  const inDemo = !!demo && (pathname === wsHref(demo) || pathname.startsWith(`${wsHref(demo)}/`));
  const member = auth.orgs.some((o) => o.workspace === demo);
  return inDemo && auth.user && !member ? <InApp demo={demo!} home={auth.org?.workspace ?? null} /> : null;
}

function InApp({ demo, home }: { demo: string; home: string | null }) {
  const band = useBand();
  useEffect(() => void (TOUR_ASKED && startTour(demo)), [demo]);
  const link = "t-label border-background hover:bg-background hover:text-foreground cursor-pointer border px-3 py-1 hover:no-underline";
  return (
    <div ref={band} role="region" aria-label="Demo workspace" className="bg-foreground text-background sticky top-0 z-30">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2 md:px-6">
        <span className="min-w-0 flex-1 text-sm">
          <span className="sm:hidden">sopops with sample data.</span>
          <span className="max-sm:hidden">You're looking at sopops with sample data: a restaurant group's four voice agents and 300 of their calls. Explore freely.</span>
        </span>
        <button type="button" onClick={() => startTour(demo)} className={link}>
          Take the tour
        </button>
        <HrefLink href={home ? wsHref(home) : "/"} className={`${link} text-background`}>
          Back to your workspace
        </HrefLink>
      </div>
    </div>
  );
}
