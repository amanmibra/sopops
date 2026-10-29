// Demo mode: the real UI over recorded API answers, for a public link with no server behind it. A build with
// VITE_SOPOPS_DEMO=1 (`pnpm build:demo`) answers every request from demo-data/, which scripts/demo/record.py fills by
// opening each page of a walkthrough run. Reads come from the recording; nothing is written, and a write says so.
import { toast } from "sonner";
import { client } from "@/client/client.gen";
import { appHref } from "./paths";

export const DEMO = import.meta.env.VITE_SOPOPS_DEMO === "1";

/** The workspace the recording was made in. */
export const DEMO_WS = import.meta.env.VITE_SOPOPS_DEMO_WS ?? "harbor-lane";

/** `?tour` on the address the app was opened at (the landing page's "See the demo" links): start the tour from its first
 *  step, whether or not this browser has seen it. Read once at load, before a redirect drops the query; taken off the
 *  address so a reload doesn't start it over. */
export const TOUR_ASKED = (() => {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("tour")) return false;
  url.searchParams.delete("tour");
  window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  return true;
})();

/** The public demo (the demo build) on the site that serves this app: sopops.dev's app is built with "/demo/". The app
 *  opens it for someone signed in when the server has no demo workspace of its own. Unset: no public demo. */
export const DEMO_URL = import.meta.env.VITE_SOPOPS_DEMO_URL || null;

/** Where people go to try sopops on their own agents. */
export const SIGN_UP_URL = import.meta.env.VITE_SOPOPS_DEMO_SIGN_UP ?? "https://app.sopops.dev/sign-up";

type Index = { answers: Record<string, string>; paths: Record<string, string> };

/** A request's key in the recording: the method, the path, the query sorted, and for a body a hash of it. The same
 * function runs in examples/walkthrough/record_demo.py, so the two must agree. */
export async function demoKey(method: string, url: URL, body: string | null): Promise<string> {
  const params = [...url.searchParams.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  const query = params.length ? `?${new URLSearchParams(params).toString()}` : "";
  const hash = body ? ` ${(await sha1(body)).slice(0, 16)}` : "";
  return `${method} ${url.pathname}${query}${hash}`;
}

async function sha1(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

let index: Promise<Index> | null = null;
const loadIndex = () => (index ??= fetch(appHref("/demo-data/index.json")).then((r) => r.json() as Promise<Index>));

const json = (status: number, data: unknown) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

/** Answers a request from the recording: the exact request, or for a read the same path with other filters, or 404. A
 * write that wasn't recorded (anything but a preview the pages make on their own) is refused, and says why. */
async function answer(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const body = request.method === "GET" || request.method === "HEAD" ? null : await request.clone().text();
  const { answers, paths } = await loadIndex();
  const file = answers[await demoKey(request.method, url, body || null)] ?? (request.method === "GET" ? paths[url.pathname] : undefined);
  if (file) {
    const r = await fetch(appHref(`/demo-data/${file}`));
    const { status, data } = (await r.json()) as { status: number; data: unknown };
    return status === 204 ? new Response(null, { status }) : json(status, data);
  }
  if (request.method === "GET") return json(404, { detail: "that isn't part of this demo" });
  // An editor's live preview isn't a save: no toast while someone types, just why the preview stopped.
  if (url.pathname.endsWith("/preview")) return json(403, { detail: "the demo can't build edited instructions. Sign up to try it on your own agents" });
  toast.info("This is a demo, so nothing is saved.", {
    id: "demo-write",
    description: "Sign up to try it on your own agents.",
    action: { label: "Sign up", onClick: () => window.open(SIGN_UP_URL, "_blank", "noopener") },
  });
  return json(403, { detail: "this is a demo, so nothing is saved. Sign up to try it on your own agents" });
}

/** Every API call the generated client makes goes to the recording. */
export function installDemo() {
  client.setConfig({ fetch: (input: RequestInfo | URL, init?: RequestInit) => answer(new Request(input, init)) });
}
