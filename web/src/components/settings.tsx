// Settings: its own side menu of sections, each on its own route (/w/<ws>/settings/<section>), and the heading every
// section opens with. Settings belongs to no mode, so the menu keeps `?from=<mode>` and leads back to that mode.
import { useLocation } from "@tanstack/react-router";
import { ArrowLeft, Menu, X } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { MODES, modeHref } from "@/lib/mode";
import { cn } from "@/lib/utils";
import { type SettingsSection, settingsHref, useWs } from "@/lib/workspace";
import { PageTitle, Sub } from "./bits";
import { HrefLink } from "./href-link";
import { useMode } from "./layout";
import { Button } from "./ui/button";

const MENU: { heading: string; items: { section: SettingsSection; label: string }[] }[] = [
  {
    heading: "Connect",
    items: [
      { section: "platforms", label: "Agents and platforms" },
      { section: "github", label: "GitHub" },
      { section: "access", label: "Access" },
      { section: "members", label: "Members" },
    ],
  },
  {
    heading: "Instructions",
    items: [
      { section: "defaults", label: "Instruction defaults" },
      { section: "history", label: "History" },
    ],
  },
  {
    heading: "QA",
    items: [{ section: "checks", label: "Checks" }],
  },
];

const LABEL = Object.fromEntries(MENU.flatMap((g) => g.items.map((i) => [i.section, i.label]))) as Record<SettingsSection, string>;

/** The section the path is on, e.g. "github" for /w/acme/settings/github. */
function sectionOf(pathname: string): SettingsSection | undefined {
  const s = /^\/w\/[^/]+\/settings\/([^/?#]+)/.exec(pathname)?.[1];
  return s && s in LABEL ? (s as SettingsSection) : undefined;
}

/** Settings' side menu. At phone width it folds into a menu button. */
export function SettingsNav() {
  const { ws } = useWs();
  const mode = useMode(ws);
  const { pathname } = useLocation();
  const here = sectionOf(pathname);
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [pathname]);
  const back = MODES.find((m) => m.mode === mode)?.label ?? "QA";
  return (
    <nav
      className="bg-card border-b px-2.5 pt-3 pb-3 md:sticky md:top-[53px] md:h-[calc(100vh-53px)] md:overflow-auto md:border-r md:border-b-0 md:pt-3.5 md:pb-10"
      aria-label="Settings"
    >
      <div className="flex items-center gap-2 md:mb-3">
        <HrefLink href={modeHref(ws, mode)} className="text-muted-foreground flex items-center gap-1.5 rounded-md px-2 py-1 text-[13px] hover:bg-muted hover:no-underline">
          <ArrowLeft className="size-3.5" /> Back to {back}
        </HrefLink>
        <span className="flex-1" />
        <Button
          size="sm"
          variant="outline"
          className="md:hidden"
          aria-expanded={open}
          aria-controls="settings-menu"
          onClick={() => setOpen((o) => !o)}
        >
          {open ? <X /> : <Menu />} {here ? LABEL[here] : "Settings"}
        </Button>
      </div>
      <div id="settings-menu" className={cn("max-md:mt-3", !open && "max-md:hidden")}>
        <h2 className="px-2 pb-2 text-[15px] font-semibold max-md:hidden">Settings</h2>
        {MENU.map((g) => (
          <div key={g.heading} className="mb-[18px] max-md:mb-3">
            <h3 className="text-muted-foreground px-2 pb-1 text-[13px] font-semibold">{g.heading}</h3>
            {g.items.map((i) => (
              <HrefLink
                key={i.section}
                href={`${settingsHref(ws, i.section)}?from=${mode}`}
                aria-current={here === i.section ? "page" : undefined}
                className={cn(
                  "text-foreground flex min-w-0 items-center gap-2 rounded-md px-2 py-1 hover:bg-muted hover:no-underline",
                  here === i.section && "bg-brand-soft hover:bg-brand-soft",
                )}
              >
                <span className="flex-1 truncate">{i.label}</span>
              </HrefLink>
            ))}
          </div>
        ))}
      </div>
    </nav>
  );
}

/** A settings section's opening: a heading that says what it's for, and a sentence or two. */
export function SectionHead({ title, aside, children }: { title: ReactNode; aside?: ReactNode; children?: ReactNode }) {
  return (
    <>
      <PageTitle aside={aside}>{title}</PageTitle>
      {children ? <Sub className="mb-6">{children}</Sub> : <div className="mb-6" />}
    </>
  );
}
