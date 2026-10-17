import { createFileRoute } from "@tanstack/react-router";
import { ImportStart } from "@/components/onboarding/start";

export const Route = createFileRoute("/w/$ws/import/")({ component: ImportStart });
