// The count beside a tab's title: short in its pill, in full with its unit
// for a hovering pointer and a reader.
import type { ReactNode } from "react";

export function TabBadge({
  label,
  children,
}: {
  /** The count in full, as "14 files". */
  label: string;
  /** What the pill shows; the label when absent. */
  children?: ReactNode;
}) {
  return (
    <span
      title={label}
      className="min-w-5 rounded-full bg-active px-1.5 py-px text-center text-2xs text-fg-2 tabular-nums"
    >
      <span aria-hidden>{children === undefined ? label : children}</span>
      <span className="sr-only">{label}</span>
    </span>
  );
}
