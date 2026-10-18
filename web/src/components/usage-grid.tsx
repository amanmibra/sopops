// A grid of rows (blocks) against columns (agents), sticky headers, scrolling both ways. The Overview edits it; the
// import review reads it.
import type { ReactNode } from "react";

export type GridRow = { id: string; head: ReactNode; cell: (column: string) => ReactNode };
export type GridSection = { key: string; title: ReactNode; rows: GridRow[] };

export function UsageGrid({ columns, sections, cellClass = "min-w-[110px]" }: { columns: { id: string; head: ReactNode }[]; sections: GridSection[]; cellClass?: string }) {
  return (
    <div className="bg-card max-h-[70vh] overflow-auto rounded-xl border">
      <table className="min-w-full border-collapse">
        <thead>
          <tr>
            <th className="bg-card sticky top-0 left-0 z-20 border-b" />
            {columns.map((c) => (
              <th key={c.id} scope="col" className="bg-card sticky top-0 z-10 border-b px-2 py-2.5 text-center text-[13px] font-semibold whitespace-nowrap">
                {c.head}
              </th>
            ))}
          </tr>
        </thead>
        {sections.map((s) =>
          s.rows.length ? (
            <tbody key={s.key}>
              <tr>
                <th colSpan={columns.length + 1} scope="colgroup" className="bg-muted text-muted-foreground border-b px-3 py-1.5 text-left text-xs font-semibold">
                  {s.title}
                </th>
              </tr>
              {s.rows.map((r) => (
                <tr key={r.id}>
                  <th scope="row" className="bg-card sticky left-0 border-b px-3 py-1.5 text-left font-medium whitespace-nowrap">
                    {r.head}
                  </th>
                  {columns.map((c) => (
                    <td key={c.id} className={`${cellClass} border-b border-l p-0 text-center`}>
                      {r.cell(c.id)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          ) : null,
        )}
      </table>
    </div>
  );
}
