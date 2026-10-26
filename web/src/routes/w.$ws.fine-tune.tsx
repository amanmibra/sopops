import { createFileRoute } from "@tanstack/react-router";
import { FineTuneView } from "@/components/fine-tune";

export const Route = createFileRoute("/w/$ws/fine-tune")({ component: FineTuneView });
