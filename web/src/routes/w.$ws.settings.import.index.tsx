import { createFileRoute } from "@tanstack/react-router";
import { ImportStart } from "@/components/onboarding/start";

/** Bringing agents in, inside settings so the rail stays put when you click it there. */
export const Route = createFileRoute("/w/$ws/settings/import/")({ component: ImportStart });
