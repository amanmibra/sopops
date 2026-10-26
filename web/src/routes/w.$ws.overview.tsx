import { createFileRoute } from "@tanstack/react-router";
import { OverviewView } from "@/components/blocks-overview";

export const Route = createFileRoute("/w/$ws/overview")({ component: OverviewView });

