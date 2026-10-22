// A column of entries beside what the entry shown shows, as the Runs tab has
// it. The column stays in view as the page scrolls, as tall as the frame's
// body, on a surface of its own, the file tree's.
import type { ReactNode } from "react";

/** An entry of the column, the one shown marked aria-current="page". */
export const sideEntry =
  "target flex items-center gap-2 rounded-row px-2 py-1.5 font-medium text-fg-2 hover:bg-hover hover:text-fg aria-[current=page]:bg-active aria-[current=page]:text-fg aria-[current=page]:shadow-current";

const column =
  "sticky top-0 flex max-h-frame flex-col gap-0.5 self-start overflow-y-auto rounded-panel bg-tree px-2 py-3 -mx-2";

export function SideColumn({
  label,
  entries,
  children,
}: {
  /** The column's name, as a reader announces it. */
  label: string;
  entries: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="grid grid-cols-[var(--spacing-runs)_minmax(0,1fr)] gap-6">
      <nav aria-label={label} className={column}>
        {entries}
      </nav>
      {children}
    </div>
  );
}
