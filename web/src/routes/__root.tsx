import type { QueryClient } from "@tanstack/react-query";
import { Outlet, createRootRouteWithContext } from "@tanstack/react-router";
import { TopBar } from "@/components/layout";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: () => (
    <TooltipProvider>
      <TopBar />
      <Outlet />
      <Toaster position="bottom-center" richColors={false} closeButton />
    </TooltipProvider>
  ),
  notFoundComponent: () => (
    <main className="px-8 py-6">
      <h1 className="text-[22px] font-semibold">Not found</h1>
      <p className="text-muted-foreground">
        There's no page here. <a href="/">Go home</a>.
      </p>
    </main>
  ),
});
