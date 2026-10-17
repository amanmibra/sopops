import { createFileRoute } from "@tanstack/react-router";
import { ConvertView } from "@/components/views";

export const Route = createFileRoute("/w/$ws/convert")({ component: ConvertView });

