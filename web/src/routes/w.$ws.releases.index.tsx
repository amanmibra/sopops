import { createFileRoute } from "@tanstack/react-router";
import { ReleasesView } from "@/components/views";

export const Route = createFileRoute("/w/$ws/releases/")({ component: ReleasesView });

