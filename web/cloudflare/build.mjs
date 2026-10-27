// `pnpm build:cloudflare`: the UI for the sopops-app Worker (wrangler.jsonc), under its base in dist-cloudflare/.
//
// The Worker answers its paths as they are, with no router taking the base off: sopops.dev/app/* is routed to it as is.
// So the build goes under the base inside the assets directory (dist-cloudflare/app/index.html,
// dist-cloudflare/app/assets/...), and index.html is copied to the root of the directory as well, because Workers
// Static Assets answers every page that isn't a file (/app/w/demo) with /index.html.
//
// SOPOPS_WEB_BASE (default /app/) and SOPOPS_WEB_API_BASE (default /api) are read by vite.config.ts. SOPOPS_WEB_OUT
// (default dist-cloudflare) is where it goes: `pnpm build:cloudflare-demo` builds the demo (VITE_SOPOPS_DEMO=1) under
// /demo/ in dist-demo-cloudflare/ for the sopops-demo Worker (wrangler.demo.jsonc), its recording beside it.
import { copyFileSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { build } from "vite";

const web = fileURLToPath(new URL("..", import.meta.url));
const out = `${web}${process.env.SOPOPS_WEB_OUT ?? "dist-cloudflare"}`;
process.env.SOPOPS_WEB_BASE ??= "/app/";
process.env.SOPOPS_WEB_API_BASE ??= "/api";
const base = `/${process.env.SOPOPS_WEB_BASE.replace(/^\/+|\/+$/g, "")}/`.replace(/^\/\/$/, "/");

rmSync(out, { recursive: true, force: true });
await build({ root: web, build: { outDir: `${out}${base}`, emptyOutDir: true } });
if (base !== "/") copyFileSync(`${out}${base}index.html`, `${out}/index.html`);
writeFileSync(`${out}/_headers`, readFileSync(`${web}cloudflare/_headers`, "utf8").replaceAll("/{base}/", base));
