// The guided tour: a spotlight on one part of a real page and a card that says what it is, with Back and Next. Next
// opens the step's page itself, so a visitor sees the whole loop (edit, publish, check, review, fix, results) without
// knowing where anything is. It runs over the demo (lib/demo.ts) and over any workspace holding the walkthrough's data.
import { useLocation } from "@tanstack/react-router";
import { type ReactNode, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { useAuth } from "@/lib/auth";
import { DEMO, DEMO_URL, DEMO_WS, SIGN_UP_URL } from "@/lib/demo";
import { appPath } from "@/lib/paths";
import { cn } from "@/lib/utils";
import { wsHref } from "@/lib/workspace";
import { useGo } from "./href-link";
import { Button } from "./ui/button";

type Step = {
  /** The page, under the workspace ("" is Home), or `follow`: open `from` and take the first `link` whose row matches. */
  page?: string;
  follow?: { from: string; link: string; row?: RegExp };
  /** What to light up: every element matching (and whose text matches `text`) as one area, or with `one` the first.
   * None: the card sits mid-screen. */
  target?: string;
  text?: RegExp;
  one?: boolean;
  title: string;
  body: ReactNode;
};

const STEPS: Step[] = [
  {
    page: "",
    target: '[data-tour="needs-you"]',
    title: "A restaurant group's phone agents",
    body: (
      <>
        <p>
          This demo is a made-up restaurant group, Harbor Lane: four restaurants whose phones are answered by voice agents on LiveKit. Their 300 calls are made up too, but sopops worked
          through them the way it would for real.
        </p>
        <p>Home is where the team starts: mistakes to look at, fixes to decide on, and edits that aren't live yet.</p>
      </>
    ),
  },
  {
    page: "agents/bayleaf-bistro",
    target: '[data-field="blocks"]',
    title: "An agent is built from blocks",
    body: (
      <p>
        Bayleaf Bistro's instructions are a few lines of its own plus shared blocks: the host role, the brand voice, and procedures like Allergen check. All four restaurants use the same
        blocks, so an edit to one reaches every agent on its next call.
      </p>
    ),
  },
  {
    page: "agents/bayleaf-bistro",
    target: '[aria-label="Draft instructions"]',
    title: "The text the agent gets",
    body: <p>sopops compiles the blocks into the exact instructions the agent receives, and shows how they differ from what's live now. A saved edit is a draft until someone publishes it.</p>,
  },
  {
    page: "procedures/allergen-check",
    target: '[data-field="steps"], [data-field="never"], [data-field="warning_signs"]',
    title: "Procedures are what calls are checked against",
    body: <p>A procedure has a goal, steps in order, rules the agent must never break, and warning signs that call for a person. Every call is checked against each procedure that came up in it.</p>,
  },
  {
    page: "procedures/allergen-check",
    target: '[data-tour="publish"]',
    title: "Publish when it's ready",
    body: <p>Saved drafts collect here. Publishing makes a numbered release, and agents fetch the live release when a call starts, so nothing has to be redeployed.</p>,
  },
  {
    page: "releases",
    target: "main table",
    title: "Every release, and a way back",
    body: <p>Each publish is a numbered release, and the live one is marked. If a release makes things worse, roll back to an earlier one from its row and the next call gets it.</p>,
  },
  {
    page: "sessions",
    target: "main table",
    title: "Every call comes back",
    body: <p>When a call ends, the agent sends it to sopops. Each one is checked against the release it ran on: followed, partly followed, or deviated.</p>,
  },
  {
    follow: { from: "sessions", link: 'a[href*="/sessions/"]', row: /deviated/i },
    target: '[data-tour="session-checks"]',
    title: "Checked step by step, with quotes",
    body: <p>Each procedure that applied gets a result for every step and every never rule, with the line from the call that shows it. Select a result to find that line in the transcript.</p>,
  },
  {
    page: "patterns",
    target: "[data-card]",
    one: true,
    title: "Misses become mistakes to review",
    body: (
      <p>
        Findings from many calls are grouped by rule and by how they went wrong. The team gives each one a verdict: a real mistake, acceptable here, the procedure is wrong, or sopops got it
        wrong. A real mistake gets a fix.
      </p>
    ),
  },
  {
    follow: { from: "suggestions?status=applied", link: 'a[href*="/suggestions/"]', row: /delivery address without checking the zone/i },
    target: '[data-tour="fix-change"]',
    title: "sopops writes the fix",
    body: (
      <p>
        Agents had been taking delivery addresses without checking the delivery zone first. sopops wrote this edit to Delivery orders, and below it shows how each agent's instructions
        change. Apply it as a draft, or as a pull request to your repo, then publish.
      </p>
    ),
  },
  {
    page: "patterns?tab=fixed",
    target: "[data-state-card]",
    text: /delivery address without checking the zone/i,
    one: true,
    title: "Fixed, and it held",
    body: (
      <p>
        The fix went out in release 3, and sopops kept checking delivery calls against it. None of them repeated the mistake, so it's marked fixed, with how many calls it took to be
        sure. If it comes back, it returns to Needs review.
      </p>
    ),
  },
  {
    page: "results",
    target: '[data-tour="kpis"]',
    title: "Did it help?",
    body: <p>Results compares the last 7 days with the 7 before. Each number opens the calls behind it, and Release compare puts two releases side by side.</p>,
  },
  {
    page: "",
    target: '[data-tour="fixes-did"]',
    title: "The loop, closed",
    body: (
      <p>
        Each fixed mistake, with how often it happened before its release and after. That's the loop: edit the instructions, publish, check every call, review what went wrong, and fix
        it.
      </p>
    ),
  },
];

// --- state: which step, kept for the tab so a reload carries on ----------------------------------------------------

type State = { ws: string; step: number } | null;
const KEY = "sopops.tour";
const listeners = new Set<() => void>();
let state: State = read();

function read(): State {
  try {
    const s = JSON.parse(sessionStorage.getItem(KEY) ?? "null") as State;
    return s && typeof s.ws === "string" && Number.isInteger(s.step) && s.step >= 0 && s.step < STEPS.length ? s : null;
  } catch {
    return null;
  }
}

function set(s: State) {
  state = s;
  try {
    if (s) sessionStorage.setItem(KEY, JSON.stringify(s));
    else sessionStorage.removeItem(KEY);
  } catch {
    // no storage: the tour still runs, it just won't survive a reload
  }
  listeners.forEach((l) => l());
}

export const startTour = (ws: string) => set({ ws, step: 0 });

/** Where someone takes the tour: the recording's workspace in the demo build; signed in, the server's demo workspace
 *  (SOPOPS_DEMO_WORKSPACE), else the public demo (DEMO_URL) in a new tab with the tour running. Signed out, nowhere:
 *  the sign-in and sign-up pages say nothing of it. */
export type TourWay = { ws: string } | { href: string };

export function useTourWay(): TourWay | null {
  const auth = useAuth().data;
  if (DEMO) return { ws: DEMO_WS };
  if (!auth?.user) return null;
  if (auth.demo_workspace) return { ws: auth.demo_workspace };
  return DEMO_URL ? { href: `${DEMO_URL}?tour` } : null;
}

export const takeTour = (way: TourWay) => ("ws" in way ? startTour(way.ws) : void window.open(way.href, "_blank", "noopener"));

/** A line for a workspace with nothing in it yet: the tour shows the loop on the demo's calls. */
export function TourPrompt() {
  const way = useTourWay();
  const here = useLocation().pathname;
  if (!way || ("ws" in way && here.startsWith(wsHref(way.ws)))) return null;
  return (
    <>
      See how sopops works on sample calls first:{" "}
      <button type="button" onClick={() => takeTour(way)} className="text-primary cursor-pointer hover:underline">
        take the tour
      </button>
      .
    </>
  );
}
export const tourRunning = () => state !== null;
const subscribe = (l: () => void) => (listeners.add(l), () => void listeners.delete(l));

/** The tour, over whatever page it's on. Mounted once, at the root. */
export function Tour() {
  const t = useSyncExternalStore(subscribe, () => state);
  return t ? <Running key={t.ws} ws={t.ws} n={t.step} /> : null;
}

// --- finding things on the page --------------------------------------------------------------------------------------

type Box = { top: number; left: number; bottom: number; right: number };
const PAD = 6;
const GAP = 12;
const EDGE = 16;

const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function query(step: Step): Element[] {
  if (!step.target) return [];
  const all = [...document.querySelectorAll(step.target)].filter((e) => e.getClientRects().length > 0 && (!step.text || step.text.test(e.textContent ?? "")));
  return step.one ? all.slice(0, 1) : all;
}

/** The area around `els`, cut off under the sticky top bar unless that's where they are. */
function boxOf(els: Element[]): Box | null {
  if (!els.length) return null;
  const rs = els.map((e) => e.getBoundingClientRect());
  const bar = els.some((e) => e.closest("[data-topbar]")) ? 0 : (document.querySelector("[data-topbar]")?.getBoundingClientRect().bottom ?? 0);
  return {
    top: Math.max(bar, Math.min(...rs.map((r) => r.top)) - PAD),
    left: Math.min(...rs.map((r) => r.left)) - PAD,
    bottom: Math.max(...rs.map((r) => r.bottom)) + PAD,
    right: Math.max(...rs.map((r) => r.right)) + PAD,
  };
}

const same = (a: Box | null, b: Box | null) => a === b || (!!a && !!b && a.top === b.top && a.left === b.left && a.bottom === b.bottom && a.right === b.right);

/** Polls until `get` finds something, or gives up after `ms`. */
async function waitFor<T>(get: () => T | null | undefined, ms: number, alive: () => boolean): Promise<T | null> {
  const until = Date.now() + ms;
  while (alive() && Date.now() < until) {
    const v = get();
    if (v) return v;
    await new Promise((r) => setTimeout(r, 100));
  }
  return null;
}

const here = () => appPath(window.location.pathname) + window.location.search;
const hrefOf = (ws: string, page: string) => (page ? `${wsHref(ws)}/${page}` : wsHref(ws));

function linkIn(follow: NonNullable<Step["follow"]>, matching: boolean): HTMLAnchorElement | null {
  const links = [...document.querySelectorAll<HTMLAnchorElement>(`main ${follow.link}`)];
  if (!matching || !follow.row) return links[0] ?? null;
  return links.find((a) => follow.row!.test((a.closest("tr, li") ?? a.parentElement ?? a).textContent ?? "")) ?? null;
}

// --- the running tour ------------------------------------------------------------------------------------------------

function Running({ ws, n }: { ws: string; n: number }) {
  const go = useGo();
  const mine = useAuth().data?.org?.workspace;
  const home = mine && mine !== ws ? wsHref(mine) : null; // the tour ran in the demo: the last step goes back
  const step = STEPS[n];
  const last = n === STEPS.length - 1;
  const followed = useRef<Record<number, string>>({}); // where each `follow` led, so Back returns to the same page
  const [ready, setReady] = useState(false);
  const [box, setBox] = useState<Box | null>(null);

  // Open the step's page, wait for what it lights up, and bring that into view.
  useEffect(() => {
    let alive = true;
    const live = () => alive;
    setReady(false);
    void (async () => {
      let href = step.page != null ? hrefOf(ws, step.page) : followed.current[n];
      if (!href && step.follow) {
        const from = hrefOf(ws, step.follow.from);
        if (here() !== from) await go(from);
        const f = step.follow;
        const link = (await waitFor(() => linkIn(f, true), 3000, live)) ?? linkIn(f, false);
        href = link ? appPath(new URL(link.href).pathname) : from;
        followed.current[n] = href;
      }
      if (!alive) return;
      if (here() !== href) await go(href);
      const els = await waitFor(() => (step.target ? query(step) : []).at(0), 6000, live);
      if (!alive) return;
      els?.scrollIntoView({ block: "center", behavior: reduced() ? "auto" : "smooth" });
      setReady(true);
    })();
    return () => {
      alive = false;
    };
  }, [n, ws]); // eslint-disable-line react-hooks/exhaustive-deps -- one run per step

  // Follow the target as the page scrolls, loads or reflows.
  useEffect(() => {
    if (!ready) return setBox(null);
    let raf = 0;
    const tick = () => {
      const b = boxOf(query(step));
      setBox((prev) => (same(prev, b) ? prev : b));
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [ready, step]);

  const to = (i: number) => (i < 0 ? undefined : i >= STEPS.length ? set(null) : set({ ws, step: i }));
  const finish = () => set(null);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest("input, textarea, select, [contenteditable=true]")) return;
      if (e.key === "Escape") finish();
      else if (e.key === "ArrowRight") to(n + 1);
      else if (e.key === "ArrowLeft") to(n - 1);
      else return;
      e.preventDefault();
      e.stopPropagation();
    };
    window.addEventListener("keydown", key, true);
    return () => window.removeEventListener("keydown", key, true);
  });

  return (
    <>
      <Shade box={box} />
      <Card n={n} step={step} box={box} waiting={!ready}>
        <div className="flex items-center gap-2 border-t px-4 py-3">
          <button type="button" onClick={finish} className="text-muted-foreground hover:text-foreground mr-auto cursor-pointer text-sm">
            {last ? "Close" : "End tour"}
          </button>
          {n > 0 && (
            <Button variant="outline" size="sm" onClick={() => to(n - 1)}>
              Back
            </Button>
          )}
          {last && DEMO ? (
            <Button size="sm" asChild>
              <a href={SIGN_UP_URL} target="_blank" rel="noopener" className="hover:no-underline" onClick={finish}>
                Try it on your agents
              </a>
            </Button>
          ) : last && home ? (
            <Button size="sm" autoFocus onClick={() => (finish(), void go(home))}>
              Back to your workspace
            </Button>
          ) : (
            <Button size="sm" autoFocus onClick={() => to(n + 1)}>
              {last ? "Done" : "Next"}
            </Button>
          )}
        </div>
      </Card>
    </>
  );
}

/** Dims everything but the target. Four panes rather than a mask, so the target stays clickable and the rest isn't. */
function Shade({ box }: { box: Box | null }) {
  const dim = "fixed z-[60] bg-[oklch(0.2_0.02_180/0.5)]";
  if (!box) return <div className={cn(dim, "inset-0")} aria-hidden="true" />;
  const top = Math.max(0, box.top);
  const left = Math.max(0, box.left);
  return (
    <div aria-hidden="true">
      <div className={dim} style={{ top: 0, left: 0, right: 0, height: top }} />
      <div className={dim} style={{ top: Math.max(top, box.bottom), left: 0, right: 0, bottom: 0 }} />
      <div className={dim} style={{ top, left: 0, width: left, height: Math.max(0, box.bottom - top) }} />
      <div className={dim} style={{ top, left: box.right, right: 0, height: Math.max(0, box.bottom - top) }} />
      <div className="border-primary pointer-events-none fixed z-[60] border-2" style={{ top: box.top, left: box.left, width: box.right - box.left, height: box.bottom - box.top }} />
    </div>
  );
}

/** The step's card: beside the target where it fits (below, above, right, left), else in the corner. */
function Card({ n, step, box, waiting, children }: { n: number; step: Step; box: Box | null; waiting: boolean; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [h, setH] = useState(0);
  const [vw, setVw] = useState(window.innerWidth);
  const [vh, setVh] = useState(window.innerHeight);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setH(el.offsetHeight);
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(el);
    return () => watch.disconnect();
  }, []);
  useEffect(() => {
    const resize = () => (setVw(window.innerWidth), setVh(window.innerHeight));
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  // Focus the card's main button on each step, without scrolling the page under it.
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("[autofocus], a[href]")?.focus({ preventScroll: true });
  }, [n]);

  const w = Math.min(400, vw - EDGE * 2);
  const ceiling = (document.querySelector("[data-topbar]")?.getBoundingClientRect().bottom ?? 0) + GAP; // under the sticky top bar
  const clampX = (x: number) => Math.min(Math.max(x, EDGE), vw - w - EDGE);
  const clampY = (y: number) => Math.min(Math.max(y, ceiling), vh - h - EDGE);
  let pos = { top: Math.max(ceiling, (vh - h) / 2), left: (vw - w) / 2 };
  if (box) {
    const fits = [
      box.bottom + GAP + h <= vh - EDGE && { top: box.bottom + GAP, left: clampX(box.left) },
      box.top - GAP - h >= ceiling && { top: box.top - GAP - h, left: clampX(box.left) },
      box.right + GAP + w <= vw - EDGE && { top: clampY(box.top), left: box.right + GAP },
      box.left - GAP - w >= EDGE && { top: clampY(box.top), left: box.left - GAP - w },
    ].find(Boolean);
    pos = fits || { top: vh - h - EDGE, left: vw - w - EDGE };
  }

  return (
    <div
      ref={ref}
      role="dialog"
      aria-labelledby="tour-title"
      className="bg-card text-card-foreground fixed z-[61] border border-foreground shadow-[0_12px_32px_oklch(0.2_0.02_180/0.25)] motion-safe:transition-[top,left] motion-safe:duration-200"
      style={{ ...pos, width: w }}
    >
      <div className="flex gap-1 px-4 pt-4" role="progressbar" aria-valuemin={1} aria-valuemax={STEPS.length} aria-valuenow={n + 1} aria-label="Tour progress">
        {STEPS.map((_, i) => (
          <span key={i} className={cn("h-1 flex-1", i <= n ? "bg-primary" : "bg-border")} />
        ))}
      </div>
      <div key={n} className="px-4 pt-3 pb-4 motion-safe:animate-in motion-safe:fade-in motion-safe:duration-300">
        <h2 id="tour-title" className="t-lead">
          {step.title}
        </h2>
        <div className={cn("mt-2 grid gap-2 text-sm leading-relaxed", waiting && "opacity-60")}>{step.body}</div>
      </div>
      {children}
    </div>
  );
}
