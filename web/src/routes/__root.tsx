import type { QueryClient } from "@tanstack/react-query";
import { Outlet, createRootRouteWithContext, useLocation } from "@tanstack/react-router";
import { AuthGate } from "@/components/account";
import { TopBar } from "@/components/layout";
import { SupportBanner } from "@/components/support";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { appHref } from "@/lib/paths";

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: Root,
  notFoundComponent: () => (
    <main className="px-8 py-6">
      <h1 className="text-2xl font-semibold">Not found</h1>
      <p className="text-muted-foreground">
        There's no page here. <a href={appHref("/")}>Go home</a>.
      </p>
    </main>
  ),
});

/** Every page: behind the sign-in gate (when sign-in is on), with the top bar except on the sign-in, sign-up, invite, join and setup pages. */
function Root() {
  const { pathname } = useLocation();
  const bare = ["/sign-in", "/sign-up", "/invite", "/join", "/welcome"].includes(pathname) || /^\/w\/[^/]+\/setup$/.test(pathname);
  return (
    <TooltipProvider>
      <AuthGate>
        {(auth) => (
          <>
            <SupportBanner auth={auth} />
            {!bare && <TopBar auth={auth} />}
            <Outlet />
          </>
        )}
      </AuthGate>
      <Toaster position="bottom-center" richColors={false} closeButton />
    </TooltipProvider>
  );
}
