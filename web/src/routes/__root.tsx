import type { QueryClient } from "@tanstack/react-query";
import { Outlet, createRootRouteWithContext, useLocation } from "@tanstack/react-router";
import { AuthGate } from "@/components/account";
import { TopBar } from "@/components/layout";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: Root,
  notFoundComponent: () => (
    <main className="px-8 py-6">
      <h1 className="text-[22px] font-semibold">Not found</h1>
      <p className="text-muted-foreground">
        There's no page here. <a href="/">Go home</a>.
      </p>
    </main>
  ),
});

/** Every page: behind the sign-in gate (when sign-in is on), with the top bar except on the sign-in, email link and invite pages. */
function Root() {
  const { pathname } = useLocation();
  const bare = pathname === "/sign-in" || pathname === "/sign-in/email" || pathname === "/invite";
  return (
    <TooltipProvider>
      <AuthGate>
        {(auth) => (
          <>
            {!bare && <TopBar auth={auth} />}
            <Outlet />
          </>
        )}
      </AuthGate>
      <Toaster position="bottom-center" richColors={false} closeButton />
    </TooltipProvider>
  );
}
