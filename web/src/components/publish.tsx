// Publishing: a dialog that lists every saved draft change, with its diff and how each agent's instructions change,
// names any form with edits that aren't saved yet (they aren't part of the release), and only then publishes.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { call, q, refresh, sdk } from "@/lib/api";
import { plural } from "@/lib/kinds";
import { useUnsaved } from "@/lib/unsaved";
import { WsContext, makeWs, useWs, wsHref } from "@/lib/workspace";
import { Banner, ErrorBanner, IssueList, Note } from "./bits";
import { author } from "./form";
import { HrefLink, useGo } from "./href-link";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";
import { ChangedAgent, ChangedItem } from "./views";

/** The edits open forms haven't saved: they stay in the form and aren't published. */
export function UnsavedBanner({ lead }: { lead?: string }) {
  const unsaved = useUnsaved();
  if (!unsaved.length) return null;
  return (
    <Banner tone="warn">
      {lead ?? "Not saved yet, so not part of this release:"}{" "}
      <b>{unsaved.map((u) => u.label).join(", ")}</b>. Save it as a draft first to publish those edits too.
    </Banner>
  );
}

function PublishBody({ onDone }: { onDone: () => void }) {
  const { ws, draft: d, releases } = useWs();
  const qc = useQueryClient();
  const go = useGo();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const changed = d.agents.filter((a) => a.status !== "unchanged");
  const errors = d.issues.filter((i) => i.severity === "error");
  const next = Math.max(0, ...releases.map((r) => r.number)) + 1;
  const blocked = busy || errors.length > 0 || d.items.length === 0;

  const publish = async () => {
    setBusy(true);
    try {
      const r = await call(sdk.createRelease({ path: { workspace: ws }, body: { author: author(), note: note.trim() || null } }));
      toast.success(r.created ? `Published release #${r.release.number}. Agents get it on their next session.` : "Nothing new to publish.");
      await refresh(qc, ws);
      onDone();
      go(wsHref(ws, "releases", r.release.number));
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{d.items.length ? "Publish these changes?" : "Nothing to publish"}</DialogTitle>
        <DialogDescription>
          {d.items.length
            ? `${plural(d.items.length, "saved draft change", "saved draft changes")}${changed.length ? `, changing ${plural(changed.length, "agent's instructions", "agents' instructions")}` : ", no agent's instructions change"}. ${d.release ? `Agents are on release #${d.release} until you publish.` : "Nothing is live yet."}`
            : "There are no saved draft changes since the live release."}
        </DialogDescription>
      </DialogHeader>
      <div className="-mx-1 max-h-[60vh] overflow-y-auto px-1">
        <UnsavedBanner />
        {errors.length > 0 && (
          <Banner tone="err">
            Fix these before publishing:
            <IssueList issues={errors} />
          </Banner>
        )}
        {d.items.length > 0 && (
          <>
            <h3 className="mb-2 text-sm font-semibold">What was edited</h3>
            {d.items.map((i) => (
              <ChangedItem key={`${i.kind}:${i.id}`} i={i} open={d.items.length === 1} />
            ))}
            <h3 className="mt-4 mb-2 text-sm font-semibold">{changed.length ? "How agents' instructions change" : "No agent's instructions change"}</h3>
            {changed.length ? changed.map((a) => <ChangedAgent key={a.agent} a={a} open={changed.length === 1} />) : <Note>The saved edits don't change what any agent is told.</Note>}
          </>
        )}
        {error != null && <ErrorBanner error={error} lead="It can't be published yet:" />}
      </div>
      <DialogFooter className="flex-wrap items-center gap-2 sm:justify-start">
        {d.items.length > 0 && (
          <Input
            className="w-72 max-w-full"
            placeholder="Release note, e.g. holiday hours"
            aria-label="Release note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !blocked && publish()}
          />
        )}
        <HrefLink href={wsHref(ws, "changes")} className="text-[13px]" onClick={onDone}>
          Open the full Changes page
        </HrefLink>
        <span className="flex-1" />
        <Button variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        {d.items.length > 0 && (
          <Button disabled={blocked} onClick={publish}>
            Publish release #{next}
          </Button>
        )}
      </DialogFooter>
    </>
  );
}

export function PublishDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">{open && <PublishBody onDone={() => onOpenChange(false)} />}</DialogContent>
    </Dialog>
  );
}

/** The top bar's button. It sits outside the workspace pages, so it loads the workspace itself (from the cache). */
export function PublishButton({ ws }: { ws: string }) {
  const [open, setOpen] = useState(false);
  const config = useQuery(q.config(ws));
  const draft = useQuery(q.draft(ws));
  const releases = useQuery(q.releases(ws));
  const unsaved = useUnsaved();
  const value = useMemo(
    () => (config.data && draft.data && releases.data ? makeWs(ws, config.data.cfg, draft.data, releases.data, config.data.legacy) : null),
    [ws, config.data, draft.data, releases.data],
  );
  const n = draft.data?.items.length ?? 0;
  return (
    <>
      <Button variant="outline" size="sm" disabled={!value} onClick={() => setOpen(true)} title={unsaved.length ? `Unsaved edits: ${unsaved.map((u) => u.label).join(", ")}` : undefined}>
        Publish changes
        {n > 0 && (
          <span className="bg-warn text-card rounded-full px-1.5 text-xs font-semibold" aria-label={`${n} saved draft changes`}>
            {n}
          </span>
        )}
        {unsaved.length > 0 && <span className="bg-err size-[7px] rounded-full" aria-label="unsaved edits" />}
      </Button>
      {value && (
        <WsContext.Provider value={value}>
          <PublishDialog open={open} onOpenChange={setOpen} />
        </WsContext.Provider>
      )}
    </>
  );
}
