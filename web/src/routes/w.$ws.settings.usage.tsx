import { createFileRoute } from "@tanstack/react-router";
import { CallUsagePanel } from "@/components/call-usage";
import { SectionHead } from "@/components/settings";

export const Route = createFileRoute("/w/$ws/settings/usage")({ component: Usage });

function Usage() {
  return (
    <>
      <SectionHead title="See how many calls you send">The calls your agents sent to sopops in a month, or a range you pick, and how they fall by day.</SectionHead>
      <CallUsagePanel />
    </>
  );
}
