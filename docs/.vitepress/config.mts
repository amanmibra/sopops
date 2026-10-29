import { posix } from "node:path";
import { defineConfig } from "vitepress";

const REPO = "https://github.com/sopops-dev/sopops";

// Where the site lives: "/" by default (pnpm build), "/docs/" on sopops.dev (pnpm build:cloudflare). The build goes
// under the same path in .vitepress/dist, so the Worker serving that folder (wrangler.jsonc) answers /docs/... itself,
// and a router in front passes the path through as it is.
const BASE = `/${(process.env.DOCS_BASE ?? "/").replace(/^\/+|\/+$/g, "")}/`.replace(/^\/\/$/, "/");

// Guides and API pages link to each other (connect.md, ../api/sessions.md), to API.md, and to code elsewhere in the
// repo (../sdks/python). On the site, a page in docs/ becomes its path, API.md becomes /api/, and anything else links
// to the file on GitHub. Links resolve against the folder of the page they're on.
function siteLink(href: string, page: string): string {
  if (/^([a-z]+:|#|\/)/i.test(href)) return href;
  const [path, hash = ""] = href.split("#");
  const target = posix.normalize(posix.join("docs", posix.dirname(page), path));
  const anchor = hash ? `#${hash}` : "";
  if (target === "API.md") return `/api/${anchor}`;
  if (target.startsWith("docs/") && target.endsWith(".md")) {
    const route = target.slice(5, -3);
    return `/${route === "index" ? "" : route.endsWith("/index") ? route.slice(0, -5) : route}${anchor}`;
  }
  if (target.startsWith("docs/") && !target.startsWith("docs/.vitepress")) return href;
  return `${REPO}/blob/main/${target}${anchor}`;
}

export default defineConfig({
  base: BASE,
  outDir: `.vitepress/dist${BASE}`,
  title: "sopops docs",
  description: "Set up sopops, connect your voice agents, and read what every call got wrong.",
  cleanUrls: true,
  lastUpdated: true,
  srcExclude: ["README.md", "node_modules/**"],
  head: [
    ["link", { rel: "icon", type: "image/svg+xml", href: `${BASE}favicon.svg` }],
    ["link", { rel: "preconnect", href: "https://fonts.googleapis.com" }],
    ["link", { rel: "preconnect", href: "https://fonts.gstatic.com", crossorigin: "" }],
    ["link", { rel: "stylesheet", href: "https://fonts.googleapis.com/css2?family=Inter+Tight:wght@500;600&family=IBM+Plex+Mono:wght@400;600&display=swap" }],
  ],
  markdown: {
    config(md) {
      // A Python block followed by a TypeScript one becomes tabs on the site. The source stays two plain fences, so
      // the same file still reads correctly on GitHub.
      md.core.ruler.before("block", "pair_languages", (state) => {
        state.src = state.src.replace(
          /^```(?:python|py)\n([\s\S]*?)^```[ \t]*\n+^```(?:ts|typescript)\n([\s\S]*?)^```[ \t]*$/gm,
          (_whole: string, py: string, ts: string) =>
            `::: code-group\n\`\`\`python [Python]\n${py}\`\`\`\n\n\`\`\`ts [TypeScript]\n${ts}\`\`\`\n:::`,
        );
      });
      md.core.ruler.push("site_links", (state) => {
        const page = (state.env as { relativePath?: string }).relativePath ?? "";
        for (const block of state.tokens) {
          for (const t of block.children ?? []) {
            if (t.type !== "link_open") continue;
            const href = t.attrGet("href");
            if (href) t.attrSet("href", siteLink(href, page));
          }
        }
      });
    },
  },
  themeConfig: {
    logo: "/favicon.svg",
    siteTitle: "sopops",
    nav: [
      { text: "Get started", link: "/first-agent" },
      { text: "API", link: "/api/" },
      { text: "sopops.dev", link: "https://sopops.dev" },
    ],
    sidebar: [
      { text: "Tutorial", items: [
        { text: "Run the local demo", link: "/first-agent" },
      ] },
      { text: "Concepts", items: [
        { text: "How sopops improves agents", link: "/what-is-sopops" },
        { text: "How agent instructions are built", link: "/instructions-model" },
        { text: "Sessions and review", link: "/sessions-and-review" },
      ] },
      { text: "How-to guides", items: [
        { text: "Import your agents", link: "/import" },
        { text: "Connect your agent", link: "/connect" },
        { text: "A/B tests", link: "/ab-tests" },
        { text: "Instructions in git", link: "/git" },
      ] },
      { text: "Self-hosting", collapsed: true, items: [
        { text: "Set up sopops", link: "/quickstart" },
        { text: "Configuration", link: "/configuration" },
        { text: "Sign-in", link: "/sign-in" },
      ] },
      { text: "Reference", collapsed: true, items: [
        { text: "SDKs", link: "/sdk" },
        { text: "Overview", link: "/api/" },
        { text: "Agents and config", link: "/api/agents-and-config" },
        { text: "Releases", link: "/api/releases" },
        { text: "Sessions", link: "/api/sessions" },
        { text: "Checks and review", link: "/api/checks-and-review" },
        { text: "Insights", link: "/api/insights" },
        { text: "Teams and access", link: "/api/teams-and-access" },
        { text: "Import", link: "/api/import" },
        { text: "GitHub", link: "/api/github" },
      ] },
    ],
    socialLinks: [{ icon: "github", link: REPO }],
    search: { provider: "local" },
    editLink: { pattern: `${REPO}/edit/main/docs/:path`, text: "Edit this page on GitHub" },
    outline: { level: [2, 3], label: "On this page" },
    footer: { message: "sopops is open source under Apache-2.0." },
  },
});
