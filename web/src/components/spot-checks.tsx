// Spot checks: rule results the checker passed, sampled for a reviewer to look at, close calls first. Keyboard-driven
// like the verdict queue: 1 says the checker was right, 2 onward pick the right status, j / k move, n adds a note.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ruleName } from "@/lib/insights";
import { plural, when } from "@/lib/kinds";
import { KIND_STATUSES, RULE_STATUS, type RuleStatus, type SpotCheckItem, isFinding, review, rq, statusPhrase } from "@/lib/review";
import { cn } from "@/lib/utils";
import { useWs, wsHref } from "@/lib/workspace";
import { Banner, ErrorBanner, RuleText, Tag } from "./bits";
import { author } from "./form";
import { HrefLink } from "./href-link";
import { Skel } from "./insights/kit";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";

const keyOf = (i: SpotCheckItem) => `${i.session_id}/${i.procedure_id}/${i.rule_id}`;

/** The checker's status first (`1`: it was right), then the others in the kind's order. */
export const answersFor = (i: SpotCheckItem): RuleStatus[] => [i.status, ...KIND_STATUSES[i.kind].filter((s) => s !== i.status)];

type Answer = { status: RuleStatus; phase: "sending" | "sent" | "error"; error?: unknown };

function Why({ item }: { item: SpotCheckItem }) {
  const chips: string[] = [];
  if (item.why.includes("close_call") && item.decision_p != null && item.decision_threshold != null)
    chips.push(`Close call: ${item.decision_p.toFixed(2)} against a threshold of ${item.decision_threshold.toFixed(2)}`);
  if (item.why.includes("review_changed") && item.decided_status) chips.push(`The reviewer model changed it from ${statusPhrase(item.decided_status)}`);
  if (!chips.length) chips.push("Picked at random");
  return (
    <span className="flex flex-wrap gap-1">
      {chips.map((c) => (
        <span key={c} className={cn("rounded-full px-2 py-px text-xs", item.why.includes("sample") ? "bg-muted text-muted-foreground" : "bg-warn-soft text-warn")}>
          {c}
        </span>
      ))}
    </span>
  );
}

function SpotCheckCard({
  item,
  focused,
  answer,
  onFocus,
  onPick,
  onChange,
  noteRef,
}: {
  item: SpotCheckItem;
  focused: boolean;
  answer?: Answer;
  onFocus: () => void;
  onPick: (s: RuleStatus, note: string) => void;
  onChange: () => void;
  noteRef: React.RefObject<HTMLTextAreaElement | null>;
}) {
  const { ws } = useWs();
  const [note, setNote] = useState("");
  const [noting, setNoting] = useState(false);
  const answers = answersFor(item);
  const turn = item.evidence.find((e) => typeof e.turn === "number")?.turn;
  const qs = new URLSearchParams({ procedure: item.procedure_id, rule: item.rule_id, ...(turn != null ? { turn: String(turn) } : {}) });
  const href = `${wsHref(ws, "sessions", item.session_id)}?${qs}`;
  const head = (
    <span className="min-w-0 flex-1 truncate font-medium">
      {ruleName(item.kind, item.index)}: <RuleText text={item.text} />
    </span>
  );
  if (answer) {
    const right = answer.status === item.status;
    return (
      <article data-card={keyOf(item)} tabIndex={-1} onFocus={onFocus} className={cn("bg-card rounded-lg border px-4 py-2 outline-none", focused && "border-foreground")}>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {answer.phase === "sent" ? <Check className="text-ok size-4 shrink-0" /> : answer.phase === "error" ? <X className="text-err size-4 shrink-0" /> : <Loader2 className="text-muted-foreground size-4 shrink-0 animate-spin" />}
          {head}
          {right ? <span className="text-muted-foreground text-xs">Checker was right</span> : <Tag className={RULE_STATUS[answer.status].tone}>{RULE_STATUS[answer.status].label}</Tag>}
          {answer.phase !== "sending" && (
            <Button size="xs" variant="ghost" onClick={onChange}>
              Change
            </Button>
          )}
        </div>
        {answer.phase === "sent" && !right && isFinding(answer.status) && <p className="text-muted-foreground mt-1 pl-7 text-sm">Kept as a mistake the checker missed. Insights still count what the checker found.</p>}
        {answer.phase === "error" && (
          <div className="mt-2 pl-7">
            <ErrorBanner error={answer.error} />
          </div>
        )}
      </article>
    );
  }
  return (
    <article
      data-card={keyOf(item)}
      tabIndex={-1}
      onFocus={onFocus}
      onClick={onFocus}
      aria-label={`${ruleName(item.kind, item.index)} of ${item.procedure_name ?? item.procedure_id}`}
      className={cn("bg-card scroll-mt-20 rounded-lg border outline-none", focused ? "border-foreground" : "hover:border-input")}
    >
      <div className="border-b bg-[var(--band)] px-4 py-3">
        <div className="text-muted-foreground mb-1 flex flex-wrap items-center gap-x-2 text-xs">
          <span className="text-k-procedure font-medium">{item.procedure_name ?? item.procedure_id}</span>
          <span aria-hidden>·</span>
          <HrefLink href={href} title="Open the session at this rule">
            Session {when(item.started_at)}
          </HrefLink>
          <span className="ml-auto">
            <Why item={item} />
          </span>
        </div>
        <blockquote className="border-k-procedure/50 mt-1 border-l-2 pl-2 text-sm">
          <span className="text-muted-foreground">{ruleName(item.kind, item.index)}:</span> <RuleText text={item.text} />
        </blockquote>
        <p className="mt-2 text-sm">
          The checker said <Tag className={RULE_STATUS[item.status].tone}>{RULE_STATUS[item.status].label}</Tag>
          {item.reason && <span className="text-muted-foreground"> {item.reason}</span>}
        </p>
        {item.evidence.length > 0 && (
          <ul className="mt-2 grid gap-1">
            {item.evidence.slice(0, 3).map((e, i) => (
              <li key={i} className="text-sm">
                <span className="text-muted-foreground mr-2 font-mono text-xs">{e.source === "tool_calls" ? "tool call" : `#${e.turn}`}</span>“{e.quote}”
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="bg-muted/40 px-4 py-3">
        <h4 className="mb-2 text-xs font-semibold">What actually happened?</h4>
        <div className="flex flex-wrap items-center gap-2">
          {answers.map((s, i) => (
            <Button
              key={s}
              size="sm"
              variant="outline"
              onClick={() => {
                onPick(s, note.trim());
                setNote("");
                setNoting(false);
              }}
            >
              <kbd className="text-muted-foreground rounded border px-1 font-mono text-sm leading-4">{i + 1}</kbd>
              {i === 0 ? (
                <>
                  Right: <Tag className={RULE_STATUS[s].tone}>{RULE_STATUS[s].label}</Tag>
                </>
              ) : (
                <Tag className={RULE_STATUS[s].tone}>{RULE_STATUS[s].label}</Tag>
              )}
            </Button>
          ))}
          {!noting && (
            <Button size="sm" variant="ghost" className="text-muted-foreground" onClick={() => setNoting(true)}>
              <kbd className="rounded border px-1 font-mono text-sm leading-4">n</kbd> Add a note
            </Button>
          )}
        </div>
        {(noting || note) && (
          <Textarea
            ref={noteRef}
            autoFocus={noting}
            className="mt-2 min-h-12 text-sm"
            placeholder="Why? (optional; kept with the answer)"
            aria-label="Note for the answer"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") (e.target as HTMLElement).blur();
            }}
          />
        )}
        {!noting && !note && <textarea ref={noteRef} className="sr-only" tabIndex={-1} aria-hidden onFocus={() => setNoting(true)} />}
      </div>
    </article>
  );
}

const typing = (t: EventTarget | null) => t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.getAttribute("role") === "combobox");

export function SpotChecks() {
  const { ws } = useWs();
  const qc = useQueryClient();
  const res = useQuery(rq.spotChecks(ws));
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const [focus, setFocus] = useState(0);
  // cards shown on this visit stay put once answered, even after the queue drops them
  const sticky = useRef(new Map<string, SpotCheckItem>());
  const noteRefs = useRef<Record<string, React.RefObject<HTMLTextAreaElement | null>>>({});
  const noteRef = (k: string) => (noteRefs.current[k] ??= { current: null });

  const fresh = res.data?.items ?? [];
  for (const i of fresh) if (!sticky.current.has(keyOf(i))) sticky.current.set(keyOf(i), i);
  const items = [...sticky.current.values()];
  const current = items[Math.min(focus, items.length - 1)];

  const pick = useCallback(
    async (item: SpotCheckItem, status: RuleStatus, note: string) => {
      const k = keyOf(item);
      setAnswers((a) => ({ ...a, [k]: { status, phase: "sending" } }));
      const at = items.findIndex((x) => keyOf(x) === k);
      const next = items.findIndex((x, i) => i > at && !answers[keyOf(x)]);
      if (next >= 0) setFocus(next);
      try {
        await review.label(ws, item.session_id, item.procedure_id, item.rule_id, { status, note: note || null, reviewer: author(), source: "spot_check" });
        setAnswers((a) => ({ ...a, [k]: { status, phase: "sent" } }));
        void qc.invalidateQueries({ queryKey: ["ws", ws, "spot-checks"] });
        void qc.invalidateQueries({ queryKey: ["ws", ws, "session", item.session_id] });
      } catch (e) {
        setAnswers((a) => ({ ...a, [k]: { status, phase: "error", error: e } }));
      }
    },
    [ws, qc, items, answers],
  );

  useEffect(() => {
    const el = current && document.querySelector<HTMLElement>(`[data-card="${CSS.escape(keyOf(current))}"]`);
    if (el && !el.contains(document.activeElement)) el.focus({ preventScroll: true });
  }, [current]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || !current) return;
      const k = keyOf(current);
      const n = Number(e.key);
      const options = answersFor(current);
      if (e.key === "j" || e.key === "ArrowDown") setFocus((f) => Math.min(f + 1, items.length - 1));
      else if (e.key === "k" || e.key === "ArrowUp") setFocus((f) => Math.max(f - 1, 0));
      else if (Number.isInteger(n) && n >= 1 && n <= options.length && !answers[k]) void pick(current, options[n - 1], noteRef(k).current?.value.trim() ?? "");
      else if (e.key === "n" && !answers[k]) noteRef(k).current?.focus();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (res.isPending)
    return (
      <div className="grid gap-3" aria-busy>
        {[0, 1, 2].map((i) => (
          <div key={i} className="bg-card grid gap-2 rounded-lg border p-4">
            <Skel className="h-4 w-1/3" />
            <Skel className="h-5 w-2/3" />
            <Skel className="h-8 w-1/2" />
          </div>
        ))}
      </div>
    );
  if (res.error)
    return (
      <Banner tone="err">
        Couldn't load the spot checks. {res.error instanceof Error ? res.error.message : ""}{" "}
        <Button size="xs" variant="outline" onClick={() => void res.refetch()}>
          Try again
        </Button>
      </Banner>
    );
  const d = res.data;
  return (
    <div className="grid gap-3">
      <p className="text-muted-foreground text-sm">
        {Math.round((d?.rate ?? 0) * 1000) / 10}% of rules the checker passed are asked about, close calls first. {plural(d?.total ?? 0, "spot check waits", "spot checks wait")}
        {d?.answered ? `, ${d.answered} answered` : ""}.
      </p>
      {!items.length ? (
        <div role="status" className="bg-card text-muted-foreground flex flex-col items-center gap-2 rounded-lg border px-4 py-10 text-center text-sm">
          <Check className="text-ok size-6" />
          <div className="text-foreground font-semibold">No spot checks waiting</div>
          <p className="max-w-[56ch]">New ones show up as sessions are checked.</p>
        </div>
      ) : (
        items.map((it, i) => (
          <SpotCheckCard
            key={keyOf(it)}
            item={it}
            focused={current != null && keyOf(current) === keyOf(it)}
            answer={answers[keyOf(it)]}
            onFocus={() => setFocus(i)}
            onPick={(s, note) => void pick(it, s, note)}
            onChange={() => setAnswers(({ [keyOf(it)]: _, ...rest }) => rest)}
            noteRef={noteRef(keyOf(it))}
          />
        ))
      )}
    </div>
  );
}
