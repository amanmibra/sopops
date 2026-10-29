import { createFileRoute } from "@tanstack/react-router";
import { PageTitle, Sub } from "@/components/bits";
import { StartSupportSession } from "@/components/support";

export const Route = createFileRoute("/support")({ component: Support });

/** Where sopops' own support staff start a session on a customer's workspace. */
function Support() {
  return (
    <main className="px-4 pt-6 pb-10 md:px-8">
      <div>
        <PageTitle>Support session</PageTitle>
        <Sub>Pick a team and how long you need.</Sub>
        <StartSupportSession />
      </div>
    </main>
  );
}
