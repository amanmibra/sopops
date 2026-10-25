import { createFileRoute, redirect } from "@tanstack/react-router";

// Results used to live under /insights: old links land on the same page under /results, keeping their filters.
export const Route = createFileRoute("/w/$ws/insights/$")({
  beforeLoad: ({ params, location }) => {
    throw redirect({ href: `/w/${encodeURIComponent(params.ws)}/results${params._splat ? `/${params._splat}` : ""}${location.searchStr}`, replace: true });
  },
});
