import { Navigate, createFileRoute } from "@tanstack/react-router";

/** An import's page moved under settings, where its rail lives. Old links (the MCP server's review link) still work. */
export const Route = createFileRoute("/w/$ws/import/$id")({
  component: function ImportMoved() {
    const { ws, id } = Route.useParams();
    return <Navigate to="/w/$ws/settings/import/$id" params={{ ws, id }} replace />;
  },
});
