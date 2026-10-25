// The one way to show JSON: a session's config and metadata, a tool call's arguments and result, a version's
// settings. Inline it reads as a shortened one-liner, as a block it's formatted and wraps to its container, and
// the whole thing opens in a dialog with a Copy button. Strings that hold JSON are formatted like objects, so
// nothing ever reads "[object Object]" or a wall of escaped quotes.
import { Braces } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Button } from "./ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "./ui/dialog";

export const isNested = (v: unknown): v is Record<string, unknown> | unknown[] => typeof v === "object" && v !== null;

/** A Python dict or list as str() prints it ({'ok': True, 'x': None}), as JSON text; null if it isn't one. */
function fromPython(s: string): string | null {
  let out = "";
  for (let i = 0; i < s.length; ) {
    const c = s[i];
    if (c === "'" || c === '"') {
      let str = "";
      let j = i + 1;
      for (; j < s.length && s[j] !== c; j++) {
        if (s[j] === "\\" && j + 1 < s.length) str += s[++j] === "n" ? "\n" : s[j] === "t" ? "\t" : s[j];
        else str += s[j];
      }
      if (j >= s.length) return null;
      out += JSON.stringify(str);
      i = j + 1;
    } else if (/[A-Za-z_]/.test(c)) {
      const word = /^[A-Za-z_]\w*/.exec(s.slice(i))![0];
      const lit = { True: "true", False: "false", None: "null" }[word];
      if (!lit) return null;
      out += lit;
      i += word.length;
    } else {
      out += c;
      i++;
    }
  }
  return out;
}

/** A string that holds a JSON (or Python-printed) object or array, parsed; anything else as it came. */
function parsed(v: unknown): unknown {
  if (typeof v !== "string" || !/^\s*[[{]/.test(v)) return v;
  try {
    return JSON.parse(v);
  } catch {
    try {
      const py = fromPython(v);
      return py == null ? v : JSON.parse(py);
    } catch {
      return v;
    }
  }
}

/** Formatted, two-space JSON. A plain string stays as it is. */
export function jsonText(v: unknown): string {
  const p = parsed(v);
  return typeof p === "string" ? p : (JSON.stringify(p, null, 2) ?? String(p));
}

/** On one line: a scalar as it is, a nested value as its JSON, shortened with its shape. */
export function jsonInline(v: unknown, max = 40): string {
  const p = parsed(v);
  if (p == null) return "—";
  if (!isNested(p)) return String(p);
  const json = JSON.stringify(p) ?? "";
  if (json.length <= max) return json;
  const n = Array.isArray(p) ? p.length : Object.keys(p).length;
  const shape = Array.isArray(p) ? `${n} item${n === 1 ? "" : "s"}` : `${n} field${n === 1 ? "" : "s"}`;
  return `${json.slice(0, max - 1)}… (${shape})`;
}

/** A value inline. Scalars look as they always did; a nested one is dimmed, with the full JSON on hover. */
export function JsonInline({ value, className }: { value: unknown; className?: string }) {
  const nested = isNested(parsed(value));
  return (
    <span className={cn("font-mono text-sm", nested && "text-muted-foreground", className)} title={nested ? jsonText(value) : undefined}>
      {jsonInline(value)}
    </span>
  );
}

/** A value as a formatted block that wraps to fit its container, never wider than it. */
export function JsonBlock({ value, prefix, tone, bare, className }: {
  value: unknown;
  /** Shown before the JSON, like "→ " for a tool's result. */
  prefix?: string;
  tone?: "err";
  /** No background, for a block that already sits on its own surface (the dialog). */
  bare?: boolean;
  className?: string;
}) {
  return (
    <pre className={cn("min-w-0 font-mono text-xs leading-5 break-words whitespace-pre-wrap", !bare && "bg-muted rounded-md px-2 py-1", tone === "err" && "text-err", className)}>
      {prefix}
      {jsonText(value)}
    </pre>
  );
}

/** The whole value as formatted JSON, behind a "View JSON" link: a header band, square corners, a Copy button. */
export function JsonDialog({ value, title, subtitle, label = "View JSON", trigger, className }: {
  value: unknown;
  title: ReactNode;
  subtitle?: ReactNode;
  label?: string;
  trigger?: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const text = jsonText(value);
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
          {subtitle && <DialogDescription className="text-xs">{subtitle}</DialogDescription>}
        </DialogHeader>
        <JsonBlock value={value} bare className="max-h-[60vh] overflow-auto px-4 py-3" />
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
