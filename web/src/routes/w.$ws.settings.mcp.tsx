import { createFileRoute } from "@tanstack/react-router";
import { McpSection } from "@/components/mcp";

/** Connecting a coding agent to this workspace over MCP. */
export const Route = createFileRoute("/w/$ws/settings/mcp")({ component: McpSection });
