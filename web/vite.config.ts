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

export default defineConfig({
  base,
  define: { "import.meta.env.VITE_SOPOPS_API_BASE": JSON.stringify(apiBase) },
  plugins: [tanstackRouter({ target: "react", autoCodeSplitting: true }), react(), tailwindcss(), apiBaseMeta()],
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
