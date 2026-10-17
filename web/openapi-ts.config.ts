import { defineConfig } from "@hey-api/openapi-ts";

// `pnpm gen:api` dumps the FastAPI spec to openapi.json and regenerates src/client from it. Both are committed.
export default defineConfig({
  input: "./openapi.json",
  output: { path: "./src/client" },
  plugins: ["@hey-api/client-fetch", "@hey-api/typescript", "@hey-api/sdk"],
});
