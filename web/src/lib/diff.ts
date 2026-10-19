// Line and word diffs by longest common subsequence, shared by instructions diffs, history and field changes.

export type Op = { op: " " | "-" | "+"; text: string };
export type Seg = { t: string; mark: boolean };
export type Row =
  | { block: false; text: string; o: number; n: number }
  | { block: true; del: { text: string; o: number; segs: Seg[] | null }[]; add: { text: string; n: number; segs: Seg[] | null }[] };

/** Lines of a text; an empty text has none (so a field filled in from nothing diffs as additions only). */
export const splitLines = (t: string | string[] | null | undefined): string[] =>
  t === null || t === undefined || t === "" ? [] : Array.isArray(t) ? t : String(t).replace(/\n$/, "").split("\n");

/** Line ops, after trimming the common ends. */
export function diffLines(a: string[], b: string[]): Op[] {
  let s = 0;
  while (s < a.length && s < b.length && a[s] === b[s]) s++;
  let ea = a.length,
    eb = b.length;
  while (ea > s && eb > s && a[ea - 1] === b[eb - 1]) {
    ea--;
    eb--;
  }
  const out: Op[] = a.slice(0, s).map((text) => ({ op: " ", text }));
  const A = a.slice(s, ea),
    B = b.slice(s, eb),
    n = A.length,
    m = B.length;
  if (n * m > 4e6) {
    // too big to align: everything in the middle changed
    A.forEach((text) => out.push({ op: "-", text }));
    B.forEach((text) => out.push({ op: "+", text }));
  } else {
    const L = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    let i = 0,
      j = 0;
    while (i < n && j < m) {
      if (A[i] === B[j]) {
        out.push({ op: " ", text: A[i] });
        i++;
        j++;
      } else if (L[i + 1][j] >= L[i][j + 1]) out.push({ op: "-", text: A[i++] });
      else out.push({ op: "+", text: B[j++] });
    }
    while (i < n) out.push({ op: "-", text: A[i++] });
    while (j < m) out.push({ op: "+", text: B[j++] });
  }
  a.slice(ea).forEach((text) => out.push({ op: " ", text }));
  return out;
}

export const diffCount = (ops: Op[]) => ({ add: ops.filter((o) => o.op === "+").length, del: ops.filter((o) => o.op === "-").length });
export const textCount = (before: string | null | undefined, after: string | null | undefined) => diffCount(diffLines(splitLines(before), splitLines(after)));

/** Words that differ between a removed line and the added line that replaced it. */
export function wordMarks(a: string, b: string): [Seg[], Seg[]] {
  const tok = (s: string) => s.match(/\s+|[\p{L}\p{N}_{}]+|[^\s\p{L}\p{N}_{}]/gu) ?? [];
  const ta = tok(a),
    tb = tok(b);
  if (ta.length * tb.length > 40000) return [[{ t: a, mark: true }], [{ t: b, mark: true }]];
  const ops = diffLines(ta, tb);
  const side = (keep: "-" | "+") => {
    const segs: Seg[] = [];
    for (const o of ops) {
      if (o.op !== " " && o.op !== keep) continue;
      const mark = o.op !== " ";
      const last = segs[segs.length - 1];
      if (last && last.mark === mark) last.t += o.text;
      else segs.push({ t: o.text, mark });
    }
    return segs;
  };
  return [side("-"), side("+")];
}

/** Rows with line numbers: unchanged lines, and blocks of removed then added lines (paired for word marks). */
export function diffRows(ops: Op[]): Row[] {
  const rows: Row[] = [];
  let o = 0,
    n = 0;
  for (let i = 0; i < ops.length; ) {
    if (ops[i].op === " ") {
      rows.push({ block: false, text: ops[i].text, o: ++o, n: ++n });
      i++;
      continue;
    }
    const del: string[] = [],
      add: string[] = [];
    while (i < ops.length && ops[i].op === "-") del.push(ops[i++].text);
    while (i < ops.length && ops[i].op === "+") add.push(ops[i++].text);
    while (i < ops.length && ops[i].op === "-") del.push(ops[i++].text);
    const pairs = Math.min(del.length, add.length);
    const marks = Array.from({ length: pairs }, (_, k) => wordMarks(del[k], add[k]));
    rows.push({
      block: true,
      del: del.map((t, k) => ({ text: t, o: ++o, segs: k < pairs ? marks[k][0] : null })),
      add: add.map((t, k) => ({ text: t, n: ++n, segs: k < pairs ? marks[k][1] : null })),
    });
  }
  return rows;
}
