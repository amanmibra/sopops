import { Outlet, createFileRoute } from "@tanstack/react-router";
import { SettingsNav } from "@/components/settings";
import { SetupBar } from "@/components/setup";
import { railCols, useRailCollapsed } from "@/lib/rail";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/w/$ws/settings")({ component: SettingsLayout });

/** Settings: its menu on the left, the open section on the right. */
function SettingsLayout() {
  const { ws } = Route.useParams();
  const collapsed = useRailCollapsed();
  return (
    <div className={cn("grid min-h-[calc(100vh-57px-var(--band-h))] grid-cols-1", railCols(collapsed))}>
      <SettingsNav />
      <main className="min-w-0 px-4 pt-5 pb-10 md:px-8 md:pt-6">
        <div className={cn("max-w-[860px]", collapsed && "mx-auto")}>
          <SetupBar ws={ws} />
          <Outlet />
        </div>
      </main>
    </div>
  );
}
