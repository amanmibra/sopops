import { Outlet, createFileRoute } from "@tanstack/react-router";
import { SettingsNav } from "@/components/settings";

export const Route = createFileRoute("/w/$ws/settings")({ component: SettingsLayout });

/** Settings: its menu on the left, the open section on the right. */
function SettingsLayout() {
  return (
    <div className="grid min-h-[calc(100vh-53px)] grid-cols-1 md:grid-cols-[264px_minmax(0,1fr)]">
      <SettingsNav />
      <main className="min-w-0 px-4 pt-5 pb-10 md:px-8 md:pt-6">
        <div className="max-w-[860px]">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
