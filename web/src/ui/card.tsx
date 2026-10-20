import type { ReactNode } from "react";

/** A bordered card with a heading row; aside sits at the heading's right. */
export function Card({
  title,
  aside,
  children,
  flush = false,
}: {
  title: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  flush?: boolean;
}) {
  return (
    <section className="overflow-hidden rounded-panel border border-line bg-card">
      <header className="flex items-baseline justify-between gap-3 border-b border-line bg-head px-5 py-3.5">
        <h2 className="flex items-center gap-2 text-md font-semibold">
          {title}
        </h2>
        {aside !== undefined && (
          <div className="text-xs text-fg-3">{aside}</div>
        )}
      </header>
      <div className={flush ? "" : "px-5 py-4"}>{children}</div>
    </section>
  );
}

/** What a view shows while it loads, when it fails and when it is empty. */
export function Note({ children }: { children: ReactNode }) {
  return <p className="p-6 text-fg-3">{children}</p>;
}
