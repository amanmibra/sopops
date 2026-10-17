import { createFileRoute } from "@tanstack/react-router";
import { OverviewView } from "@/components/views";

export const Route = createFileRoute("/w/$ws/overview")({ component: OverviewView });

