import { ChevronRight } from "lucide-react";

export function CheckMetadata({ model, promptVersion, releaseId }: { model: string | null; promptVersion: string | null; releaseId?: number | null }) {
  const models = [...new Set((model ?? "").split("+").map((name) => name.trim()).filter(Boolean))];
  if (!models.length && !promptVersion && releaseId == null) return null;
  return (
    <details className="group text-muted-foreground mt-3 text-xs">
      <summary className="hover:text-foreground focus-visible:outline-ring flex w-fit cursor-pointer list-none items-center gap-1 py-1 focus-visible:outline-2 [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-3 transition-transform group-open:rotate-90" />
        Check details
      </summary>
      <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 border-l pl-3">
        {models.length > 0 && <><dt>Models</dt>
        <dd className="min-w-0 space-y-1">{models.map((name) => <div key={name} className="break-words [overflow-wrap:anywhere]">{name}</div>)}</dd></>}
        {promptVersion && <><dt>Prompt</dt><dd className="break-words [overflow-wrap:anywhere]">{promptVersion}</dd></>}
        {releaseId != null && <><dt>Release</dt><dd>#{releaseId}</dd></>}
      </dl>
    </details>
  );
}
