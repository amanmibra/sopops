import { Navigate, createFileRoute } from "@tanstack/react-router";

/** The import page moved under settings, where its rail lives. Old links still work. */
export const Route = createFileRoute("/w/$ws/import/")({
  component: function ImportMoved() {
    const { ws } = Route.useParams();
    return <Navigate to="/w/$ws/settings/import" params={{ ws }} replace />;
  },
});
