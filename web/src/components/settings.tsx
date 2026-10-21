// Settings: its own side menu of sections, each on its own route (/w/<ws>/settings/<section>), and the heading every
// section opens with. Its Back link returns to the last workspace page you had open.
import { useLocation } from "@tanstack/react-router";
import { ArrowLeft, GitBranch, History, KeyRound, ListChecks, type LucideIcon, Menu, Plug, SlidersHorizontal, Users, X } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { lastPageHref } from "@/lib/last-page";
import { useRailCollapsed } from "@/lib/rail";
import { cn } from "@/lib/utils";
import { type SettingsSection, settingsHref, useWs } from "@/lib/workspace";
import { PageTitle, Sub } from "./bits";
import { RailFrame, RailItem } from "./layout";
import { Button } from "./ui/button";

const MENU: { heading: string; items: { section: SettingsSection; label: string; icon: LucideIcon }[] }[] = [
  {
    heading: "Connect",
    items: [
      { section: "platforms", label: "Agents and platforms", icon: Plug },
      { section: "github", label: "GitHub", icon: GitBranch },
      { section: "access", label: "Access", icon: KeyRound },
      { section: "members", label: "Members", icon: Users },
    ],
  },
  {
    heading: "Instructions",
    items: [
      { section: "defaults", label: "Instruction defaults", icon: SlidersHorizontal },
      { section: "history", label: "History", icon: History },
    ],
  },
  {
    heading: "QA",
    items: [{ section: "checks", label: "Checks", icon: ListChecks }],
  },
];

const LABEL = Object.fromEntries(MENU.flatMap((g) => g.items.map((i) => [i.section, i.label]))) as Record<SettingsSection, string>;

/** The section the path is on, e.g. "github" for /w/acme/settings/github. */
function sectionOf(pathname: string): SettingsSection | undefined {
  const s = /^\/w\/[^/]+\/settings\/([^/?#]+)/.exec(pathname)?.[1];
  return s && s in LABEL ? (s as SettingsSection) : undefined;
}

/** Settings' side menu, in the rail frame the other pages use (collapsible to icons). At phone width it folds into a
 * menu button above the section. */
export function SettingsNav() {
  const { ws } = useWs();
  const { pathname } = useLocation();
  const here = sectionOf(pathname);
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [pathname]);
  const collapsed = useRailCollapsed();
  const backLink = (c: boolean) => <RailItem collapsed={c} muted href={lastPageHref(ws)} icon={ArrowLeft} label="Back" active={false} />;
  const items = (c: boolean) =>
    MENU.map((g) => (
      <div key={g.heading} className={c ? "mb-3 grid gap-1" : "mb-[18px] max-md:mb-3"}>
        {!c && <h3 className="text-muted-foreground px-2 pb-1 text-sm font-semibold">{g.heading}</h3>}
        {g.items.map((i) => (
          <RailItem key={i.section} collapsed={c} href={settingsHref(ws, i.section)} icon={i.icon} label={i.label} active={here === i.section} />
        ))}
      </div>
    ));
  return (
    <>
      <RailFrame label="Settings" collapsed={collapsed}>
        <div className="mb-3">{backLink(collapsed)}</div>
        {!collapsed && <h2 className="px-2 pb-2 text-lg font-semibold">Settings</h2>}
        {items(collapsed)}
      </RailFrame>
      <nav className="bg-card border-b px-2 py-3 md:hidden" aria-label="Settings">
        <div className="flex items-center gap-2">
          {backLink(false)}
          <span className="flex-1" />
          <Button size="sm" variant="outline" aria-expanded={open} aria-controls="settings-menu" onClick={() => setOpen((o) => !o)}>
            {open ? <X /> : <Menu />} {here ? LABEL[here] : "Settings"}
          </Button>
        </div>
        <div id="settings-menu" className={cn("mt-3", !open && "hidden")}>
          {items(false)}
        </div>
      </nav>
    </>
  );
}
export function SectionHead({ title, aside, children }: { title: ReactNode; aside?: ReactNode; children?: ReactNode }) {
  return (
    <>
      <PageTitle aside={aside}>{title}</PageTitle>
      {children ? <Sub className="mb-6">{children}</Sub> : <div className="mb-6" />}
    </>
  );
}
