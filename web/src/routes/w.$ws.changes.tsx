import { createFileRoute } from "@tanstack/react-router";
import { ChangesView } from "@/components/views";

export const Route = createFileRoute("/w/$ws/changes")({ component: ChangesView });

