import { createFileRoute, redirect } from "@tanstack/react-router";

// Old /settings links land on the first section, keeping ?from=<mode>.
export const Route = createFileRoute("/w/$ws/settings/")({
  beforeLoad: ({ params, search }) => {
    throw redirect({ to: "/w/$ws/settings/platforms", params, search, replace: true });
  },
});
