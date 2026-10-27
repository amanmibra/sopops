import fs from "node:fs";
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { type Plugin, defineConfig } from "vite";

// Dev: `pnpm dev` on :5173 proxies the API to sopops on :8484 (`uv run sopops`).
// Prod: `pnpm build` writes dist/, which sopops serves itself.
const api = process.env.SOPOPS_API ?? "http://127.0.0.1:8484";

// Where the UI lives and where it calls the API. Both default to the site root, which is how sopops serves itself.
// A deployment that puts sopops under a path builds for it: SOPOPS_WEB_BASE=/app/ (the UI's base) and
// SOPOPS_WEB_API_BASE=/api (the API's, which defaults to the UI's base without its slash, /app). The server reads both
// back from the built index.html (sopops_server/urls.py), so it is told once.
const slashed = (p: string) => `/${p.replace(/^\/+|\/+$/g, "")}/`.replace(/^\/\/$/, "/");
const base = slashed(process.env.SOPOPS_WEB_BASE ?? "/");
const apiBase = (process.env.SOPOPS_WEB_API_BASE ?? base).replace(/\/+$/, "");

/** Names the API's base in index.html for the server, when there is one to name. */
const apiBaseMeta = (): Plugin => ({
  name: "sopops-api-base",
  transformIndexHtml: () => (apiBase ? [{ tag: "meta", attrs: { name: "sopops-api-base", content: apiBase }, injectTo: "head" }] : []),
});

/** The demo build (`pnpm build:demo`) answers the API from a recording: demo-data/, made by
 * examples/walkthrough/record_demo.py, served next to the UI. */
const demo = process.env.VITE_SOPOPS_DEMO === "1";
const demoData = path.resolve(__dirname, "demo-data");
const demoRecording = (): Plugin => ({
  name: "sopops-demo-data",
  configureServer: (server) =>
    void server.middlewares.use((req, res, next) => {
      const m = req.url?.match(/^\/demo-data\/([\w.-]+)$/);
      if (!m || !fs.existsSync(path.join(demoData, m[1]))) return next();
      res.setHeader("Content-Type", "application/json");
      fs.createReadStream(path.join(demoData, m[1])).pipe(res);
    }),
  writeBundle: (opts) => {
    if (!fs.existsSync(demoData)) throw new Error("No demo-data/: record it first (just examples/walkthrough/demo).");
    fs.cpSync(demoData, path.join(opts.dir!, "demo-data"), { recursive: true });
  },
});

export default defineConfig({
  base,
  define: { "import.meta.env.VITE_SOPOPS_API_BASE": JSON.stringify(apiBase) },
  plugins: [tanstackRouter({ target: "react", autoCodeSplitting: true }), react(), tailwindcss(), apiBaseMeta(), ...(demo ? [demoRecording()] : [])],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    host: "127.0.0.1",
    // The API's paths, under its base when it has one: the proxy takes the base off and says so, as a router would.
    proxy: Object.fromEntries(
      ["/v1", "/docs", "/openapi.json"].map((p) => [
        apiBase + p,
        apiBase ? { target: api, rewrite: (url: string) => url.slice(apiBase.length), headers: { "X-Forwarded-Prefix": apiBase } } : api,
      ]),
    ),
  },
});
