// An item's versions, and what changed from a picked one to the current draft.
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { type ItemVersion, call, sdk } from "@/lib/api";
import { type Kind, when } from "@/lib/kinds";
import { setRestore } from "@/lib/restore";
import { cn } from "@/lib/utils";
import { useWs } from "@/lib/workspace";
import { ErrorBanner, Note, Sub, Tag } from "./bits";
import { ChangeList } from "./change-list";
import { DiffView } from "./diff-view";
import { useGo } from "./href-link";
import { ItemHeader, Missing, Page } from "./item-shell";
import { Button } from "./ui/button";

type ListKind = Exclude<Kind, "settings">;
const HISTORY = { agent: sdk.configAgentHistory, instruction: sdk.configInstructionHistory, procedure: sdk.configProcedureHistory, group: sdk.configGroupHistory };
const VERSION = { agent: sdk.configAgentVersion, instruction: sdk.configInstructionVersion, procedure: sdk.configProcedureVersion, group: sdk.configGroupVersion };

/** `head` replaces the item header: Settings brings its own. */
export function HistoryView({ kind, id, head: custom }: { kind: Kind; id: string; head?: ReactNode }) {
  const { ws, model, releases, href } = useWs();
  const go = useGo();
  const item = model.find(kind, id) as Record<string, unknown> | undefined;
  const [pick, setPick] = useState<number | null>(null);
  const hist = useQuery({
    queryKey: ["ws", ws, "history", kind, id],
    queryFn: (): Promise<ItemVersion[]> =>
      kind === "settings" ? call(sdk.settingsHistory({ path: { workspace: ws } })) : call(HISTORY[kind as ListKind]({ path: { workspace: ws, id } })),
  });
  const list = hist.data ?? [];
  const index = pick ?? Math.min(1, Math.max(list.length - 1, 0));
  const chosen = list[index];
  const old = useQuery({
    queryKey: ["ws", ws, "version", kind, id, chosen?.version, chosen?.file],
    enabled: !!chosen,
    queryFn: (): Promise<Record<string, unknown>> =>
      kind === "settings"
        ? call(sdk.settingsVersion({ path: { workspace: ws, version: chosen.version } }))
        : call(VERSION[kind as ListKind]({ path: { workspace: ws, id, version: chosen.version }, query: { file: chosen.file } })),
  });
  if (!item && kind !== "settings") return <Missing kind={kind} id={id} />;
  const title = kind === "settings" ? "Settings" : model.itemName(kind, id);
  const head = custom ?? <ItemHeader kind={kind} id={id} title={title} tab="history" />;
  if (hist.isPending) return <Page>{head}<p className="text-muted-foreground">Loading…</p></Page>;
  if (hist.isError) {
    const notFound = (hist.error as { status?: number }).status === 404;
    return <Page>{head}{notFound ? <Note>Nothing saved yet.</Note> : <ErrorBanner error={hist.error} />}</Page>;
  }
  const live = releases.find((r) => r.current)?.files ?? {};
  const label = (i: number) => `Version ${list.length - i}`;
  const current = index === 0;
  const o = old.data;
  return (
    <Page>
      {head}
      <div className="grid items-start gap-5 md:grid-cols-[280px_minmax(0,1fr)]">
        <table className="w-full border-collapse" aria-label="Versions">
          <tbody>
            {list.map((h, i) => (
              <tr key={`${h.file}:${h.version}`} className={cn("cursor-pointer hover:bg-muted", i === index && "bg-muted")} onClick={() => setPick(i)}>
                <td className="border-b px-2.5 py-2 align-top">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <b>{label(i)}</b>
                    {i === 0 && <Tag>draft</Tag>}
                    {live[h.file] === h.version && <Tag tone="live">live</Tag>}
                    {h.deleted && <Tag tone="deleted">deleted</Tag>}
                  </div>
                  <Note>
                    {when(h.created_at)}
                    {h.author ? `, ${h.author}` : ""}
                  </Note>
                  {h.note && <div className="text-muted-foreground text-[13px]">{h.note}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="min-w-0">
          {current ? (
            <>
              <Sub className="mt-0">This is the current draft. Pick an older version to see what changed since.</Sub>
              <pre className="plain">{model.readable(kind, item ?? null).join("\n")}</pre>
            </>
          ) : old.isError ? (
            <ErrorBanner error={old.error} />
          ) : !o ? (
            <p className="text-muted-foreground">Loading…</p>
          ) : (
            <>
              <Sub className="mt-0">What changed from {label(index).toLowerCase()} to the current draft.</Sub>
              {o.deleted || o.legacy || !item ? (
                <DiffView before={model.readable(kind, o)} after={model.readable(kind, item ?? null)} />
              ) : (
                <>
                  <ChangeList kind={kind} before={o} after={item} />
                  <h3 className="mt-5 mb-1 font-semibold">All of it, line by line</h3>
                  <DiffView before={model.readable(kind, o)} after={model.readable(kind, item)} context={99} />
                  <div className="mt-6 flex flex-wrap items-center gap-2.5 border-t py-3">
                    <Button
                      variant="outline"
                      onClick={() => {
                        setRestore(kind, id, o, label(index));
                        go(href(kind, kind === "settings" ? undefined : id));
                      }}
                    >
                      Restore {label(index).toLowerCase()}
                    </Button>
                    <Note>Opens it in the form. Nothing changes until you save.</Note>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </Page>
  );
}
