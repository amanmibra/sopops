// Fine tune: the data people give sopops to tune its models to this workspace's calls. Its first collection point is
// the checker's spot checks: batches of rules the checker passed, and the patterns marked "Checker is wrong" on Mistakes.
import { Keyboard } from "lucide-react";
import { PageTitle, Sub } from "./bits";
import { Hint, TipBody } from "./insights/kit";
import { Page } from "./item-shell";
import { SpotChecks } from "./spot-checks";

export function FineTuneView() {
  return (
    <Page wide>
      <PageTitle>Fine tune</PageTitle>
      <Sub className="max-w-[90ch]">
        Answers you give here train sopops on your calls. Everything on this page is about the checker's calls, not the agent's mistakes: those are on Mistakes.
      </Sub>
      <h2 className="mt-6 flex items-center gap-2 text-base font-semibold">
        Checker spot checks
        <Hint tip={<TipBody head="Keyboard">j / k move between rules · 1 says the checker was right · 2 and up pick what actually happened</TipBody>}>
          <span className="text-muted-foreground ml-auto hidden items-center gap-1 text-xs font-normal sm:inline-flex">
            <Keyboard className="size-3.5" /> j k · 1–5
          </span>
        </Hint>
      </h2>
      <p className="text-muted-foreground mt-1 mb-3 max-w-[90ch] text-sm">
        Was the checker right? Batches of rules it passed, spread across procedures and agents, close calls and decisions the reviewer model changed first. Patterns you marked Checker is wrong on Mistakes are listed here too.
      </p>
      <SpotChecks />
    </Page>
  );
}
