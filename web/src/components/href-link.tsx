import { useRouter } from "@tanstack/react-router";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";
import { appHref } from "@/lib/paths";

/** A link to an app path built at runtime (item pages). Navigates through the router, so unsaved-edit guards apply. */
export function HrefLink({ href, className, onClick, ...props }: ComponentProps<"a"> & { href: string }) {
  const router = useRouter();
  return (
    <a
      href={appHref(href)}
      className={cn("text-primary hover:underline underline-offset-2", className)}
      onClick={(e) => {
        onClick?.(e);
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        router.navigate({ href });
      }}
      {...props}
    />
  );
}

export function useGo() {
  const router = useRouter();
  return (href: string, opts?: { replace?: boolean }) => router.navigate({ href, replace: opts?.replace });
}
