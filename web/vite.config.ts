import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev: `pnpm dev` on :5173 proxies the API to sopops on :8484 (`uv run sopops`).
// Prod: `pnpm build` writes dist/, which sopops serves itself.
const api = process.env.SOPOPS_API ?? "http://127.0.0.1:8484";

export default defineConfig({
  plugins: [tanstackRouter({ target: "react", autoCodeSplitting: true }), react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    host: "127.0.0.1",
    proxy: { "/v1": api, "/docs": api, "/openapi.json": api },
  },
});
