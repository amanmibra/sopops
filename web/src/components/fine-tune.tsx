// Fine tune: the data people give sopops to tune its models to this workspace's calls. Its first collection point is
// the checker's spot checks: batches of rules the checker passed, and the patterns marked "Checker is wrong" on Mistakes.
// A hosted feature: open-source sopops shows what it is and where to get it (an extension turns on `fine_tune`).
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Keyboard } from "lucide-react";
import { q } from "@/lib/api";
import { wsHref, useWs } from "@/lib/workspace";
import { PageTitle, Sub } from "./bits";
import { HrefLink } from "./href-link";
import { Hint, Skel, TipBody } from "./insights/kit";
import { Page } from "./item-shell";
import { SpotChecks } from "./spot-checks";
import { Button } from "./ui/button";

export const HOSTED_URL = "https://sopops.dev/#pricing";

/** Whether this server has hosted sopops's Fine tune. */
export function useFineTune() {
  const res = useQuery({ ...q.features(), retry: false });
  return { on: res.data?.features.includes("fine_tune") ?? false, loading: res.isPending };
}

export function FineTuneView() {
  const { on, loading } = useFineTune();
  return (
    <Page wide>
      <PageTitle>Fine tune</PageTitle>
      <Sub>
        Answers you give here train sopops on your calls. Everything on this page is about the checker's calls, not the agent's mistakes: those are on Mistakes.
      </Sub>
      {loading ? <Skel className="mt-6 h-40" /> : on ? <CheckerSpotChecks /> : <HostedOnly />}
    </Page>
  );
}

function CheckerSpotChecks() {
  return (
    <>
      <h2 className="mt-6 flex items-center gap-2 text-base font-semibold">
        Checker spot checks
        <Hint tip={<TipBody head="Keyboard">j / k move between rules · 1 says the checker was right · 2 and up pick what actually happened</TipBody>}>
          <span className="text-muted-foreground ml-auto hidden items-center gap-1 text-xs font-normal sm:inline-flex">
            <Keyboard className="size-3.5" /> j k · 1–5
          </span>
        </Hint>
      </h2>
      <p className="text-muted-foreground mt-1 mb-3 text-sm">
        Was the checker right? Batches of rules it passed, spread across procedures and agents, close calls and decisions the reviewer model changed first. Patterns you marked Checker is wrong on Mistakes are listed here too.
      </p>
      <SpotChecks />
    </>
  );
}

/** Open-source sopops: what Fine tune does, and that it comes with hosted sopops. */
function HostedOnly() {
  const { ws } = useWs();
  return (
    <section className="bg-card mt-6 rounded-lg border px-6 py-6">
      <h2 className="text-lg font-semibold">Fine tune comes with hosted sopops</h2>
      <p className="mt-2 text-sm">
        This sopops checks your calls with the models you set up. Hosted sopops also turns what your team tells it into training data, and tunes the checker to your
        calls and your procedures.
      </p>
      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-[14rem_1fr]">
        <dt className="font-medium">Checker spot checks</dt>
        <dd className="text-muted-foreground">Batches of rules the checker passed, close calls first, to say whether it was right.</dd>
        <dt className="font-medium">Checker is wrong, in one place</dt>
        <dd className="text-muted-foreground">Every pattern your team marked Checker is wrong on Mistakes, next to the spot checks.</dd>
        <dt className="font-medium">A checker tuned to you</dt>
        <dd className="text-muted-foreground">Models trained on your answers, and more ways to collect them as they ship.</dd>
      </dl>
      <p className="text-muted-foreground mt-4 text-sm">
        Here, your verdicts and corrections still recalibrate the checker's thresholds. You can still mark a pattern Checker is wrong on{" "}
        <HrefLink href={wsHref(ws, "patterns")}>Mistakes</HrefLink>.
      </p>
      <Button asChild className="mt-5">
        <a href={HOSTED_URL} target="_blank" rel="noreferrer">
          See hosted sopops <ExternalLink />
        </a>
      </Button>
    </section>
  );
}
