import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { createRoot } from "react-dom/client";
import { routeTree } from "./routeTree.gen";
import "./index.css";
import { BASEPATH } from "./lib/paths";

console.info("sopops deployment verification: ui-20261025");

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5_000, retry: (n, e) => n < 1 && !(e as { status?: number }).status, refetchOnWindowFocus: true } },
});

const router = createRouter({ routeTree, basepath: BASEPATH, context: { queryClient }, defaultPreload: false, scrollRestoration: true });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={queryClient}>
    <RouterProvider router={router} />
  </QueryClientProvider>,
);
