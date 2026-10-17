import { createFileRoute } from "@tanstack/react-router";
import { SettingsEditor } from "@/components/editors";

export const Route = createFileRoute("/w/$ws/settings/")({ component: SettingsEditor });

