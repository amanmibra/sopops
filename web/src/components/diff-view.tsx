// A line diff with word marks, unified or side by side, folding long unchanged runs.
import { type ReactNode, useMemo, useState } from "react";
import { type Row, type Seg, diffCount, diffLines, diffRows, splitLines } from "@/lib/diff";
import { plural } from "@/lib/kinds";
import { prefs } from "@/lib/prefs";
import { Counts, Segmented } from "./bits";

type Mode = "unified" | "split";

const segs = (s: Seg[] | null, text: string, cls: string): ReactNode =>
  s ? s.map((x, i) => (x.mark ? <mark key={i} className={cls}>{x.t}</mark> : x.t)) : text || " ";

export function DiffView({
  before,
  after,
  context = 3,
  toggle = true,
  empty = "No differences.",
}: {
  before: string | string[] | null | undefined;
  after: string | string[] | null | undefined;
  context?: number;
  toggle?: boolean;
  empty?: string;
}) {
  const ops = useMemo(() => diffLines(splitLines(before), splitLines(after)), [before, after]);
  const rows = useMemo(() => diffRows(ops), [ops]);
  const [mode, setMode] = useState<Mode>(() => (toggle && prefs.get("diffmode") === "split" ? "split" : "unified"));
  const [opened, setOpened] = useState<Set<number>>(new Set());
  if (!ops.some((o) => o.op !== " ")) return <p className="text-muted-foreground text-[13px] my-2">{empty}</p>;

  const line = (key: string, cls: string, o: number | null, n: number | null, sign: string, content: ReactNode) => (
    <div key={key} role="row" className={`dl ${cls}`}>
      <span className="ln">{o ?? ""}</span>
      <span className="ln">{n ?? ""}</span>
      <span className="sg" aria-hidden="true">{sign}</span>
      <span className="tx">{content}</span>
    </div>
  );
  const half = (cls: string, num: number | string, sign: string, content: ReactNode) => (
    <div className={`dh ${cls}`}>
      <span className="ln">{num}</span>
      <span className="sg" aria-hidden="true">{sign}</span>
      <span className="tx">{content}</span>
    </div>
  );
  const emit = (r: Row, key: string): ReactNode[] => {
    if (!r.block)
      return [
        mode === "unified" ? (
          line(key, "eq", r.o, r.n, " ", r.text || " ")
        ) : (
          <div key={key} role="row" className="dl split">
            {half("eq", r.o, " ", r.text || " ")}
            {half("eq", r.n, " ", r.text || " ")}
          </div>
        ),
      ];
    if (mode === "unified")
      return [
        ...r.del.map((d, k) => line(`${key}d${k}`, "del", d.o, null, "−", segs(d.segs, d.text, "wdel"))),
        ...r.add.map((a, k) => line(`${key}a${k}`, "add", null, a.n, "+", segs(a.segs, a.text, "wadd"))),
      ];
    return Array.from({ length: Math.max(r.del.length, r.add.length) }, (_, k) => {
      const d = r.del[k],
        a = r.add[k];
      return (
        <div key={`${key}s${k}`} role="row" className="dl split">
          {d ? half("del", d.o, "−", segs(d.segs, d.text, "wdel")) : half("pad", "", "", "")}
          {a ? half("add", a.n, "+", segs(a.segs, a.text, "wadd")) : half("pad", "", "", "")}
        </div>
      );
    });
  };

  const body: ReactNode[] = [];
  for (let i = 0; i < rows.length; ) {
    if (rows[i].block) {
      body.push(...emit(rows[i], `r${i}`));
      i++;
      continue;
    }
    let j = i;
    while (j < rows.length && !rows[j].block) j++;
    const run = rows.slice(i, j);
    const head = i === 0 ? 0 : context,
      tail = j === rows.length ? 0 : context;
    if (run.length > head + tail + 1 && !opened.has(i)) {
      run.slice(0, head).forEach((r, k) => body.push(...emit(r, `r${i + k}`)));
      const hidden = run.length - head - tail;
      const at = i;
      body.push(
        <button key={`f${i}`} type="button" className="dv-fold" onClick={() => setOpened(new Set([...opened, at]))}>
          Show {plural(hidden, "unchanged line", "unchanged lines")}
        </button>,
      );
      run.slice(run.length - tail).forEach((r, k) => body.push(...emit(r, `r${j - tail + k}`)));
    } else run.forEach((r, k) => body.push(...emit(r, `r${i + k}`)));
    i = j;
  }
  const c = diffCount(ops);
  return (
    <div className="my-2">
      {toggle && (
        <div className="mb-2 flex items-center gap-2.5">
          <Counts {...c} />
          <span className="flex-1" />
          <Segmented<Mode>
            small
            label="Diff layout"
            value={mode}
            options={[
              ["unified", "Unified"],
              ["split", "Side by side"],
            ]}
            onChange={(v) => {
              setMode(v);
              prefs.set("diffmode", v);
            }}
          />
        </div>
      )}
      <div className={`dv ${mode}`} role="table" aria-label="Differences">
        {body}
      </div>
    </div>
  );
}

/** A unified diff given as text (as the server makes them): its hunks, with line numbers. */
export function PatchView({ patch, empty = "No differences." }: { patch: string; empty?: string }) {
  const rows: ReactNode[] = [];
  let o = 0,
    n = 0;
  patch.split("\n").forEach((l, i) => {
    if (l.startsWith("---") || l.startsWith("+++") || (l === "" && i === patch.split("\n").length - 1)) return;
    const h = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(l);
    if (h) {
      o = Number(h[1]);
      n = Number(h[2]);
      rows.push(
        <div key={i} className="dv-fold cursor-default hover:no-underline" role="row">
          {l}
        </div>,
      );
      return;
    }
    const sign = l[0];
    const text = l.slice(1) || " ";
    const cls = sign === "+" ? "add" : sign === "-" ? "del" : "eq";
    rows.push(
      <div key={i} role="row" className={`dl ${cls}`}>
        <span className="ln">{sign === "+" ? "" : o}</span>
        <span className="ln">{sign === "-" ? "" : n}</span>
        <span className="sg" aria-hidden="true">
          {sign === "+" ? "+" : sign === "-" ? "−" : " "}
        </span>
        <span className="tx">{text}</span>
      </div>,
    );
    if (sign !== "+") o++;
    if (sign !== "-") n++;
  });
  if (!rows.length) return <p className="text-muted-foreground my-2 text-[13px]">{empty}</p>;
  return (
    <div className="dv unified my-2" role="table" aria-label="Differences">
      {rows}
    </div>
  );
}
