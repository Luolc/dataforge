import type { ReactNode } from "react";

/**
 * A bordered card with a heading row; aside sits at the heading's right, and
 * byline on a line of its own under them.
 */
export function Card({
  title,
  aside,
  byline,
  children,
  flush = false,
}: {
  title: ReactNode;
  aside?: ReactNode;
  byline?: ReactNode;
  children: ReactNode;
  flush?: boolean;
}) {
  return (
    <section className="overflow-hidden rounded-panel border border-line bg-card">
      {/* The aside goes under the title when the two do not fit. */}
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-line bg-head px-5 py-3.5">
        <h2 className="flex min-w-0 items-center gap-2 text-md font-semibold">
          {title}
        </h2>
        {aside !== undefined && (
          <div className="min-w-0 text-xs text-fg-3">{aside}</div>
        )}
        {byline !== undefined && (
          <p className="w-full min-w-0 text-xs text-fg-3">{byline}</p>
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
