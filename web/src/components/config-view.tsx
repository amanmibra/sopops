// What a session or a version ran with. A setting is a scalar, or a whole object or array (a voice pipeline's
// settings, feature flags, a nested section): scalars read inline, a nested one shows its shape and opens as
// formatted JSON in a dialog, so nothing ever reads "[object Object]".
import { Braces } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Button } from "./ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "./ui/dialog";

export const isNested = (v: unknown): v is Record<string, unknown> | unknown[] => typeof v === "object" && v !== null;

/** A setting on one line: a scalar as it is, a nested one as its JSON, shortened. */
export function settingText(v: unknown, max = 40): string {
  if (v == null) return "—";
  if (!isNested(v)) return String(v);
  const json = JSON.stringify(v) ?? "";
  if (json.length <= max) return json;
  const shape = Array.isArray(v) ? `${v.length} item${v.length === 1 ? "" : "s"}` : `${Object.keys(v).length} field${Object.keys(v).length === 1 ? "" : "s"}`;
  return `${json.slice(0, max - 1)}… (${shape})`;
}

/** One setting's value where it's shown inline. Scalars look as they always did; a nested one is dimmed JSON. */
export function SettingValue({ value, className }: { value: unknown; className?: string }) {
  const nested = isNested(value);
  return (
    <span className={cn("font-mono text-sm", nested && "text-muted-foreground", className)} title={nested ? JSON.stringify(value, null, 2) : undefined}>
      {settingText(value)}
    </span>
  );
}

/** The whole thing as formatted JSON, behind a "View JSON" link: a header band, square corners, monospace. */
export function ConfigJson({ value, title, subtitle, label = "View JSON", trigger, className }: {
  value: unknown;
  title: ReactNode;
  subtitle?: ReactNode;
  label?: string;
  trigger?: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const text = JSON.stringify(value, null, 2) ?? "";
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {trigger ?? (
          <button type="button" className={cn("text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs whitespace-nowrap underline underline-offset-2", className)}>
            <Braces className="size-3" /> {label}
          </button>
        )}
      </DialogTrigger>
      <DialogContent showCloseButton={false} className="gap-0 p-0 sm:max-w-2xl">
        <DialogHeader className="border-b bg-[var(--band)] px-4 py-2 text-left">
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="text-xs">{subtitle ?? "What the agent ran with, as sopops stored it."}</DialogDescription>
        </DialogHeader>
        <pre className="max-h-[60vh] overflow-auto px-4 py-3 font-mono text-xs leading-5 whitespace-pre">{text}</pre>
        <DialogFooter className="border-t bg-[var(--band)] px-4 py-2">
          <Button size="sm" variant="outline" onClick={() => void navigator.clipboard.writeText(text).then(() => toast.success("Copied."))}>
            Copy
          </Button>
          <DialogClose asChild>
            <Button size="sm">Close</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
