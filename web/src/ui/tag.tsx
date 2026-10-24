// A short value drawn as a pill, such as a column's value in the heading of
// the attempt's page.
import type { ReactNode } from "react";

export function Tag({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <span className="inline-flex items-baseline gap-1 rounded-full bg-active px-2 py-px text-xs text-fg-2">
      <span className="sr-only">{`${title}: `}</span>
      {children}
    </span>
  );
}
